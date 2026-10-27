"""TLI Autoloot A: the spin-off build. Same app, plus IRIS 2 auto blink:
presses Blink (A) when a skill's charge badge reads 0."""

import tli_autoloot

tli_autoloot.APP_NAME = "TLI Autoloot A"
tli_autoloot.TASK_NAME = "TLI Autoloot A"
tli_autoloot.A_ON_ZERO = True

if __name__ == "__main__":
    tli_autoloot.main()
