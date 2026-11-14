"""Point d'entrée de `python -m veille`."""
import sys

from .cli import main

sys.exit(main())
