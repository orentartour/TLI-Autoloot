"""TLI Autoloot A: the spin-off build. Same app, plus it presses A while the
skill in slot A shows 0 charges."""

import tli_qspam

tli_qspam.APP_NAME = "TLI Autoloot A"
tli_qspam.TASK_NAME = "TLI Autoloot A"
tli_qspam.A_ON_ZERO = True

if __name__ == "__main__":
    tli_qspam.main()
