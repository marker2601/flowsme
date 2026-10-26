"""Entry point of the packaged Windows app (FlowSME.exe). FlowSME.exe --self-test checks the installation."""
import sys

from flowsme.app import main

if __name__ == "__main__":
    main(check="--self-test" in sys.argv)
