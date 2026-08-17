Evaluation Plan

eval/
- the folder where the test runner and eval harness goes.
- The eval harness:
1. launches the llm using standard recipe
2. tests llm for sanity and warms it up
3. create a sandbox environment for agent to use
4. ensure agent is using the correct llm
5. run through all tests and evaluates output
6. writes data to results/{eval_name}/results.json, writes full trace from the agent to the same folder
7. Add simplified results.json to benchmark-data.json 

tests/
- The data used for the tests, each folder contain a single test. All tests should be run for all agents.

results/
- The output of the tests, subfolders are named by modelname+harnessname+date, aka eval_name

LLM Host: ssh mike@spark, started with launch-model.py
Runner: run on this local machine (macbook pro, but ensure it's isolated via docker sandbox
cli: defined in the 
