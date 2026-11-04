import sys

from eesimlab.main import main

raise SystemExit(main(sys.argv[1:] or ["--help"]))
