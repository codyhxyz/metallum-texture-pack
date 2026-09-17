#!/bin/bash
set -u
cd "$(dirname "$0")"
gen() {
  python3 generate_vanilla_albedo.py "$1" "batch2048-$1.png" --size 2048 --seed 42 $2 \
    && echo "OK $1" >> batch2048.log || echo "FAIL $1" >> batch2048.log
}
: > batch2048.log
gen cobblestone ""
gen bricks ""
gen iron_ore ""
gen oak_leaves "--grayscale"
gen oak_planks "--material wood"
echo DONE >> batch2048.log
