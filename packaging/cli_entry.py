import sys

from mscopilot.main import main

raise SystemExit(main(sys.argv[1:] or ["--help"]))
