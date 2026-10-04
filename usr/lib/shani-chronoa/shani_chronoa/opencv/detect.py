"""What is in a picture: faces (YuNet), everyday objects (NanoDet-Plus), and where the people are (PP-HumanSeg).

The pre- and post-processing for each model is OpenCV Zoo's own reference code
(Apache-2.0: nanodet.py's anchors, distribution decoding and NMS, demo.py's
letterbox; pphumanseg.py's normalisation), adapted to plain functions here -
except how NanoDet's outputs are paired, which the reference gets wrong on
OpenCV 5 (see `_levels`). A
detector is loaded once per process and kept.

Every function takes and returns OpenCV images (BGR numpy arrays) and plain
Python data, so the effects and the video reader build on them without knowing
which model answered.
"""

from __future__ import annotations

from typing import NamedTuple

from shani_chronoa.opencv import runtime

#: COCO's 80 classes, in NanoDet's output order (OpenCV Zoo, object_detection_nanodet/demo.py)
CLASSES = ('person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat',
           'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog',
           'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella',
           'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball', 'kite',
           'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'bottle',
           'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange',
           'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch', 'potted plant',
           'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone',
           'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors',
           'teddy bear', 'hair drier', 'toothbrush')


class Box(NamedTuple):
    label: str
    score: float
    x: int
    y: int
    w: int
    h: int


_CACHE: dict = {}


def faces(image, threshold: float = 0.8) -> "list[Box]":
    cv2, _np = runtime.load()
    h, w = image.shape[:2]
    detector = _CACHE.get("faces")
    if detector is None:
        detector = cv2.FaceDetectorYN.create(str(runtime.model_path(runtime.FACES)), "", (w, h), threshold, 0.3, 5000)
        _CACHE["faces"] = detector
    detector.setScoreThreshold(threshold)
    detector.setInputSize((w, h))
    _ok, found = detector.detect(image)
    if found is None:
        return []
    return [Box("face", float(f[-1]), max(0, int(f[0])), max(0, int(f[1])), int(f[2]), int(f[3])) for f in found]


# --- NanoDet-Plus (OpenCV Zoo's reference post-processing) ----------------------

_SIZE, _REG_MAX = 416, 7


def _nanodet():
    cv2, _np = runtime.load()
    if "objects" not in _CACHE:
        _CACHE["objects"] = cv2.dnn.readNet(str(runtime.model_path(runtime.OBJECTS)))
    return _CACHE["objects"]


def _anchors(stride: int):
    _cv2, np = runtime.load()
    n = _SIZE // stride
    xv, yv = np.meshgrid(np.arange(n) * stride, np.arange(n) * stride)
    return np.column_stack((xv.flatten() + 0.5 * (stride - 1), yv.flatten() + 0.5 * (stride - 1)))


def _levels(outs):
    """(stride, class scores, box distributions) per output level, whatever order the outputs came in.

    The zoo's reference pairs `outs[::2]` with `outs[1::2]`, which assumes the
    outputs alternate score, box, score, box. OpenCV 5 returns all the scores
    first (measured 2026-10-02: 2704x80, 676x80, 169x80, then the x32 boxes),
    and the 2022nov model has three levels where the reference lists four
    strides. So outputs are matched by shape instead: 80 columns are COCO's
    class scores, 4 * (reg_max + 1) = 32 are box distributions, and a level's
    stride follows from its row count (52 x 52 rows -> stride 8 at 416 px).
    """
    scores, boxes = {}, {}
    for out in outs:
        out = out.squeeze(axis=0) if out.ndim == 3 else out
        side = int(round(out.shape[0] ** 0.5))
        stride = _SIZE // side
        if out.shape[1] == len(CLASSES):
            scores[stride] = out
        elif out.shape[1] == 4 * (_REG_MAX + 1):
            boxes[stride] = out
    return [(s, scores[s], boxes[s]) for s in sorted(scores) if s in boxes]


def _letterbox(image):
    cv2, _np = runtime.load()
    h, w = image.shape[:2]
    top = left = 0
    newh = neww = _SIZE
    if h != w:
        if h > w:
            neww = int(_SIZE / (h / w))
            img = cv2.resize(image, (neww, newh), interpolation=cv2.INTER_AREA)
            left = (_SIZE - neww) // 2
            img = cv2.copyMakeBorder(img, 0, 0, left, _SIZE - neww - left, cv2.BORDER_CONSTANT, value=0)
        else:
            newh = int(_SIZE * (h / w))
            img = cv2.resize(image, (neww, newh), interpolation=cv2.INTER_AREA)
            top = (_SIZE - newh) // 2
            img = cv2.copyMakeBorder(img, top, _SIZE - newh - top, 0, 0, cv2.BORDER_CONSTANT, value=0)
    else:
        img = cv2.resize(image, (_SIZE, _SIZE), interpolation=cv2.INTER_AREA)
    return img, (top, left, newh, neww)


#: The zoo demo's 0.35 reported a second person in a one-person portrait (0.38) on
#: OpenCV's own lena.jpg; at 0.5 both sample photos come out right (2026-10-02).
OBJECT_THRESHOLD = 0.5


def objects(image, threshold: float = OBJECT_THRESHOLD, iou: float = 0.6) -> "list[Box]":
    cv2, np = runtime.load()
    net = _nanodet()
    boxed, (top, left, newh, neww) = _letterbox(image)
    mean = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
    std = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)
    net.setInput(cv2.dnn.blobFromImage((boxed.astype(np.float32) - mean) / std))
    outs = net.forward(net.getUnconnectedOutLayersNames())
    project = np.arange(_REG_MAX + 1)
    all_boxes, all_scores = [], []
    for stride, cls_score, bbox_pred in _levels(outs):
        anchors = _anchors(stride)
        e = np.exp(bbox_pred.reshape(-1, _REG_MAX + 1))
        dist = np.dot(e / e.sum(axis=1, keepdims=True), project).reshape(-1, 4) * stride
        if cls_score.shape[0] > 1000:
            keep = cls_score.max(axis=1).argsort()[::-1][:1000]
            anchors, dist, cls_score = anchors[keep], dist[keep], cls_score[keep]
        x1 = np.clip(anchors[:, 0] - dist[:, 0], 0, _SIZE)
        y1 = np.clip(anchors[:, 1] - dist[:, 1], 0, _SIZE)
        x2 = np.clip(anchors[:, 0] + dist[:, 2], 0, _SIZE)
        y2 = np.clip(anchors[:, 1] + dist[:, 3], 0, _SIZE)
        all_boxes.append(np.column_stack([x1, y1, x2, y2]))
        all_scores.append(cls_score)
    boxes, scores = np.concatenate(all_boxes), np.concatenate(all_scores)
    wh = boxes.copy()
    wh[:, 2:4] -= wh[:, 0:2]
    class_ids, confidences = scores.argmax(axis=1), scores.max(axis=1)
    keep = cv2.dnn.NMSBoxes(wh.tolist(), confidences.tolist(), threshold, iou)
    h, w = image.shape[:2]
    rh, rw = h / newh, w / neww
    found = []
    for i in (keep.flatten() if hasattr(keep, "flatten") else keep):
        bx1, by1, bx2, by2 = boxes[i]
        x1, y1 = max(0, (bx1 - left) * rw), max(0, (by1 - top) * rh)
        x2, y2 = min(w, (bx2 - left) * rw), min(h, (by2 - top) * rh)
        found.append(Box(CLASSES[int(class_ids[i])], float(confidences[i]), int(x1), int(y1), int(x2 - x1), int(y2 - y1)))
    return sorted(found, key=lambda b: -b.score)


def people_mask(image):
    """A uint8 mask the image's size: 255 where a person is, 0 elsewhere (PP-HumanSeg)."""
    cv2, np = runtime.load()
    if "people" not in _CACHE:
        _CACHE["people"] = cv2.dnn.readNet(str(runtime.model_path(runtime.PEOPLE)))
    net = _CACHE["people"]
    h, w = image.shape[:2]
    rgb = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (192, 192)).astype(np.float32) / 255.0
    net.setInput(cv2.dnn.blobFromImage((rgb - 0.5) / 0.5))
    out = net.forward()[0]
    out = cv2.resize(out.transpose(1, 2, 0), (w, h), interpolation=cv2.INTER_LINEAR).transpose(2, 0, 1)
    return (out.argmax(axis=0) == 1).astype(np.uint8) * 255


def summarise(boxes: "list[Box]") -> str:
    """'2 people, a dog and a bicycle' - counts by label, most first."""
    counts: "dict[str, int]" = {}
    for b in boxes:
        counts[b.label] = counts.get(b.label, 0) + 1
    if not counts:
        return "nothing it recognises"
    plural = {"person": "people", "bus": "buses", "knife": "knives", "couch": "couches", "sandwich": "sandwiches",
              "toothbrush": "toothbrushes", "skis": "pairs of skis", "mouse": "mice", "sheep": "sheep"}
    parts = [(f"{n} {plural.get(label, label + 's')}" if n > 1 else
              f"{'an' if label[0] in 'aeiou' else 'a'} {label}") for label, n in
             sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
