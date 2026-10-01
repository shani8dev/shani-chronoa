"""Skill: encode or decode text - base64, URL encoding, hex, binary, rot13, Morse.
Pure Python on this machine; nothing leaves it."""

import base64
import binascii
import codecs
import urllib.parse

from shani_chronoa.skills import Skill

MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....",
         "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.",
         "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
         "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
         "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--",
         "?": "..--..", "'": ".----.", "!": "-.-.--", "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...",
         ":": "---...", ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-", '"': ".-..-.",
         "@": ".--.-."}
UNMORSE = {v: k for k, v in MORSE.items()}
MAX = 10000

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "encode_text",
        "description": "Encode or decode text: base64, url (percent-encoding), hex, binary, rot13, morse.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
            "scheme": {"type": "string", "enum": ["base64", "url", "hex", "binary", "rot13", "morse"]},
            "direction": {"type": "string", "enum": ["encode", "decode"], "description": "Default encode."},
        }, "required": ["text", "scheme"]},
    },
}


def convert(text: str, scheme: str, decode: bool) -> str:
    if scheme == "base64":
        return base64.b64decode(text.strip(), validate=True).decode("utf-8") if decode \
            else base64.b64encode(text.encode()).decode()
    if scheme == "url":
        return urllib.parse.unquote(text) if decode else urllib.parse.quote(text, safe="")
    if scheme == "hex":
        return bytes.fromhex(text.replace(" ", "")).decode("utf-8") if decode else text.encode().hex(" ")
    if scheme == "binary":
        if decode:
            bits = text.replace(" ", "")
            return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)).decode("utf-8")
        return " ".join(f"{b:08b}" for b in text.encode())
    if scheme == "rot13":
        return codecs.encode(text, "rot13")
    if scheme == "morse":
        if decode:
            return " ".join("".join(UNMORSE.get(c, "?") for c in w.split()) for w in text.strip().split(" / "))
        return " / ".join(" ".join(MORSE[c] for c in w.upper() if c in MORSE) for w in text.split())
    raise ValueError(f"unknown scheme {scheme}")


def _run(arguments: dict) -> str:
    text, scheme = str(arguments.get("text") or ""), arguments.get("scheme")
    if not text:
        return "Which text?"
    if len(text) > MAX:
        return f"That is longer than {MAX} characters."
    decode = arguments.get("direction") == "decode"
    try:
        return convert(text, scheme, decode)
    except (ValueError, binascii.Error, UnicodeDecodeError) as e:
        return f"That is not valid {scheme}: {e}."


SKILLS = [Skill(name="encode_text", schema=_SCHEMA, run=_run)]
