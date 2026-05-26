import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))

from model.config import load_config, save_config, ConfigError
from model.validators import validate_coherence


def main():
    pass


if __name__ == "__main__":
    main()
