"""Face tracking via MediaPipe Face Landmarker (Tasks API, VIDEO mode, CPU).

From a single ~3.7 MB Apache-2.0 model we get, per frame:
  * head pose (yaw/pitch/roll) from the facial transformation matrix
  * per-eye blink scores from blendshapes (eyeBlinkLeft / eyeBlinkRight, 0..1)
  * a coarse gaze ratio from iris-vs-eye-corner geometry

See RESEARCH.md §3/§4/§5.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

# iris + eye-corner landmark indices (MediaPipe FaceMesh-V2, 478 pts w/ iris refinement).
# NOTE: in FaceMesh topology 468/33/133/159/145 belong to the MODEL's right eye and
# 473/263/362/386/374 to the model's left eye. With the default mirrored (selfie) frame
# the model's right eye is the USER's left eye, so these names are user-space-under-mirror.
_LEFT_IRIS = 468          # user's left eye (when mirror=True)
_RIGHT_IRIS = 473
_LEFT_EYE_OUT, _LEFT_EYE_IN = 33, 133
_RIGHT_EYE_OUT, _RIGHT_EYE_IN = 263, 362
_LEFT_EYE_TOP, _LEFT_EYE_BOT = 159, 145
_RIGHT_EYE_TOP, _RIGHT_EYE_BOT = 386, 374


@dataclass
class FaceFrame:
    present: bool = False
    yaw: float = 0.0          # degrees, + = head turned (image) right
    pitch: float = 0.0        # degrees, + = head DOWN (MediaPipe matrix convention)
    roll: float = 0.0
    blink_left: float = 0.0   # 0 open .. 1 closed — the USER's left eye (mirror-corrected)
    blink_right: float = 0.0  # 0 open .. 1 closed — the USER's right eye
    gaze_x: float = 0.0       # coarse, -1 (image-left) .. +1 (image-right)
    gaze_y: float = 0.0       # coarse, -1 (up) .. +1 (down)
    landmarks: object = None  # raw normalized landmark list (for overlay/debug)


def _rotation_to_euler(R: np.ndarray) -> tuple[float, float, float]:
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    if sy < 1e-6:  # gimbal lock
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0.0
    else:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    return math.degrees(x), math.degrees(y), math.degrees(z)


class FaceTracker:
    def __init__(self, model_path: str, num_faces: int = 1, mirror: bool = True):
        # mirror=True means frames are cv2.flip'ed BEFORE detection (selfie view), so the
        # model's "left eye" blendshape is the user's RIGHT eye — we swap to user space.
        self.mirror = mirror
        base = mp_python.BaseOptions(model_asset_path=model_path)
        opts = vision.FaceLandmarkerOptions(
            base_options=base,
            running_mode=vision.RunningMode.VIDEO,
            num_faces=num_faces,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._lm = vision.FaceLandmarker.create_from_options(opts)
        self._ts = 0  # strictly increasing timestamp for VIDEO mode

    def process(self, bgr_frame: np.ndarray, timestamp_ms: int | None = None) -> FaceFrame:
        if timestamp_ms is None or timestamp_ms <= self._ts:
            timestamp_ms = self._ts + 1
        self._ts = timestamp_ms

        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = self._lm.detect_for_video(mp_img, timestamp_ms)

        out = FaceFrame()
        if not res.face_landmarks:
            return out
        out.present = True
        out.landmarks = res.face_landmarks[0]

        # ---- head pose ----
        if res.facial_transformation_matrixes:
            M = np.array(res.facial_transformation_matrixes[0]).reshape(4, 4)
            pitch, yaw, roll = _rotation_to_euler(M[:3, :3])
            out.pitch, out.yaw, out.roll = pitch, yaw, roll

        # ---- per-eye blink (converted to USER space when the frame is mirrored) ----
        if res.face_blendshapes:
            model_left = model_right = 0.0
            for cat in res.face_blendshapes[0]:
                if cat.category_name == "eyeBlinkLeft":
                    model_left = float(cat.score)
                elif cat.category_name == "eyeBlinkRight":
                    model_right = float(cat.score)
            if self.mirror:
                out.blink_left, out.blink_right = model_right, model_left
            else:
                out.blink_left, out.blink_right = model_left, model_right

        # ---- coarse gaze from iris ----
        out.gaze_x, out.gaze_y = self._coarse_gaze(out.landmarks)
        return out

    @staticmethod
    def _coarse_gaze(lms) -> tuple[float, float]:
        def px(i):
            return lms[i].x, lms[i].y

        try:
            # horizontal: iris position between eye corners, averaged over both eyes.
            # Both eyes are measured in the SAME image direction (left-to-right corner),
            # otherwise the nasal->temporal ratios of the two eyes point opposite ways
            # and their average cancels to ~0 for any gaze.
            def hratio(iris, c1, c2):
                ix, _ = px(iris)
                x1, _ = px(c1)
                x2, _ = px(c2)
                lo, hi = min(x1, x2), max(x1, x2)
                if hi - lo < 1e-6:
                    return 0.0
                r = (ix - lo) / (hi - lo)  # 0 at image-left corner, 1 at image-right
                return r * 2 - 1

            def vratio(iris, top_i, bot_i):
                _, iy = px(iris)
                _, ty = px(top_i)
                _, by = px(bot_i)
                denom = (by - ty)
                if abs(denom) < 1e-6:
                    return 0.0
                r = (iy - ty) / denom
                return r * 2 - 1

            gx = 0.5 * (hratio(_LEFT_IRIS, _LEFT_EYE_OUT, _LEFT_EYE_IN)
                        + hratio(_RIGHT_IRIS, _RIGHT_EYE_OUT, _RIGHT_EYE_IN))
            gy = 0.5 * (vratio(_LEFT_IRIS, _LEFT_EYE_TOP, _LEFT_EYE_BOT)
                        + vratio(_RIGHT_IRIS, _RIGHT_EYE_TOP, _RIGHT_EYE_BOT))
            return max(-1.5, min(1.5, gx)), max(-1.5, min(1.5, gy))
        except Exception:
            return 0.0, 0.0

    def close(self) -> None:
        try:
            self._lm.close()
        except Exception:
            pass
