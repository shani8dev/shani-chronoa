"""Photos and videos with OpenCV: what is in them, effects on them, reading through them, and page scans.

Three tools, three jobs: tesseract reads text (`senses/ocr.py`), the vision
model says what a picture means (`local_vision`), and OpenCV works on the
pixels - finding faces, objects and people, applying effects, sampling a video,
flattening a photographed page. ImageMagick (`edit_image`) and ffmpeg
(`convert_media`) keep the plain edits and conversions.

- `runtime` - installing OpenCV, numpy and the models into the user's home, and loading them
- `detect`  - faces, 80 kinds of object, and the people mask
- `effects` - the effects, each one image in and one image out
- `video`   - keyframes, and an effect over a whole video (keeping its sound)
- `page`    - finding and flattening a photographed page
"""
