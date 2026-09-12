python launch_model.py --v --keep-alive --model Qwen3.8-27B-NVFP4-xhigh
time tool-eval-bench bench --seed 84729 --base-url  http://spark:8000 --timeout 600 --hardmode --parallel 2
#hardmode 0.1 context" 90 @ 51min 160/176
#hardmodeonly 0.75 context" 84 @ 130min

python launch_model.py --v --keep-alive --model Qwen3.8-27B-AWQ-INT4
time tool-eval-bench bench --seed 84729 --base-url  http://spark:8000  --timeout 600 --hardmode --parallel 2
#hardmode 0.1 context: 89 @ 113min. 156/176 (for -medium)
#hardmodeonly 0.75 context: 89 @ 198min


python launch_model.py --v --keep-alive --model Qwen3.8-Flash-Next-NVFP4
time tool-eval-bench bench --seed 84729 --base-url  http://spark:8000  --timeout 600 --hardmode --parallel 2

default: 93/100, 450k tks, 12min
nothink: 87/100, 382k tks, 6 min
low: 92/100, 416k, 8min
medium:93/100, 415k, 10min
xhigh: 91, 450k, 12min