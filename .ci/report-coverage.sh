#!/bin/sh

if [ -z "$SUBMIT_COVERAGE" ]; then
  exit
fi

set -x

codecov_bash=/tmp/codecov-bash.sh

if ! [ -f "$codecov_bash" ]; then
  curl -sSf --retry 5 -o "$codecov_bash" https://codecov.io/bash
  chmod +x "$codecov_bash"
fi

"$codecov_bash" -Z -X fix -f coverage.xml -n "$TOX_ENV_NAME" -F "$TRAVIS_OS_NAME"
