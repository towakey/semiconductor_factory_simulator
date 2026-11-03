#!C:/Users/kyosg/AppData/Local/Programs/Python/Python38/python.exe
# shebang は環境の python.exe に合わせる(本番は適宜書き換え)
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from apilib import cgi_main

cgi_main()
