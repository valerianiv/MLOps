PYTHON ?= python
HW67 ?= ../mlops26-hw67
.PHONY: check check-public demo-break evidence experiments release-baseline release-candidate serve smoke replay loadtest

check:
	$(PYTHON) scripts/check.py
check-public:
	$(PYTHON) -m compileall -q release.py tests
	$(PYTHON) -m unittest discover -s tests -v
demo-break:
	$(PYTHON) scripts/check.py --demo-break
evidence:
	$(PYTHON) scripts/red_green.py
experiments:
	$(PYTHON) scripts/run_experiments.py
release-baseline:
	$(PYTHON) lab.py --workspace .lab-real run --hw67 $(HW67) --role baseline
release-candidate:
	$(PYTHON) lab.py --workspace .lab-real run --hw67 $(HW67) --role candidate
serve:
	$(PYTHON) service.py --workspace .lab-real --backend transformers --port 8091 --warmup-seconds 0
smoke:
	$(PYTHON) scripts/real_api.py smoke --hw67 $(HW67)
replay:
	$(PYTHON) scripts/real_api.py replay --hw67 $(HW67)
loadtest:
	$(PYTHON) scripts/real_api.py loadtest --hw67 $(HW67)
