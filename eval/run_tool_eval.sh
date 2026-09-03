# python launch_model.py --v --fast --model Qwen3.6-35B-A3B-NVFP4
# tool-eval-bench bench --seed 42 --base-url  http://spark:8000
#86/69

# python launch_model.py --v --fast --model Qwen3.6-27B-NVFP4
# tool-eval-bench bench --seed 42 --base-url  http://spark:8000
#89/25

python launch_model.py --v --fast --model Qwen3.6-27B-FP8
tool-eval-bench bench --seed 42 --base-url  http://spark:8000

python launch_model.py --v --fast --model Qwen3.8-27B-FP8-low
tool-eval-bench bench --seed 42 --base-url  http://spark:8000

python launch_model.py --v --fast --model Qwen3.8-27B-FP8-medium
tool-eval-bench bench --seed 42 --base-url  http://spark:8000

python launch_model.py --v --fast --model Qwen3.8-27B-NVFP4-xhigh
tool-eval-bench bench --seed 42 --base-url  http://spark:8000

python launch_model.py --v --fast --model Qwen3.8-27B-AWQ-INT4
tool-eval-bench bench --seed 42 --base-url  http://spark:8000


