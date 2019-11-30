#!/bin/sh
#
# Submit/report coverage when run from Travis for "coverage" tox factors.

set -ex

coverage_xml="coverage-travis.xml"
if ! [ -f "$coverage_xml" ]; then
  exit
fi

codecov_bash=/tmp/codecov-bash.sh

if ! [ -f "$codecov_bash" ]; then
  curl -sSf --retry 5 -o "$codecov_bash" https://raw.githubusercontent.com/blueyed/codecov-bash/debug/codecov
  chmod +x "$codecov_bash"
fi

"$codecov_bash" -Z -X fix -f "$coverage_xml" -n "${TOX_ENV_NAME%-coverage}" -F "$TRAVIS_OS_NAME,${TOX_ENV_NAME%-coverage}"
rm "$coverage_xml"
