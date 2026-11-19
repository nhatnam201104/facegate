import cv2

from server.config import _apply_v4_defaults
from server.vision.runtime_threads import apply_cv_threads, ort_session_options


def test_defaults_disable_spinning_and_limit_cv_threads():
    pl = _apply_v4_defaults({})["pipeline"]
    assert pl["cv_threads"] == 2
    assert pl["ort_allow_spinning"] is False


def test_cv_threads_clamped():
    assert _apply_v4_defaults({"pipeline": {"cv_threads": 999}})["pipeline"]["cv_threads"] == 64
    assert _apply_v4_defaults({"pipeline": {"cv_threads": -3}})["pipeline"]["cv_threads"] == 0


def test_ort_session_options_spinning_flag():
    so = ort_session_options({"pipeline": {"ort_allow_spinning": False}})
    assert so.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    assert so.get_session_config_entry("session.inter_op.allow_spinning") == "0"
    so = ort_session_options({"pipeline": {"ort_allow_spinning": True}})
    assert so.get_session_config_entry("session.intra_op.allow_spinning") == "1"
    assert ort_session_options(None).get_session_config_entry("session.intra_op.allow_spinning") == "0"


def test_apply_cv_threads_sets_and_zero_keeps_default():
    before = cv2.getNumThreads()
    try:
        assert apply_cv_threads({"pipeline": {"cv_threads": 2}}) == 2
        assert apply_cv_threads({"pipeline": {"cv_threads": 0}}) == 2   # 0 = khong dong vao gia tri hien hanh
    finally:
        cv2.setNumThreads(before)
