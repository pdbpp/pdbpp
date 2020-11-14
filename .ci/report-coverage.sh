#!/bin/sh

set -ex

# Set --connect-timeout to work around https://github.com/curl/curl/issues/4461
# NOTE: use version prior to https://github.com/codecov/codecov-bash/pull/373#issuecomment-727146528
curl -S -L --connect-timeout 5 --retry 6 -s -o /tmp/codecov.sh https://raw.githubusercontent.com/codecov/codecov-bash/20201104-3e6f2fc/codecov

for _ in 1 2 3 4 5; do
  bash /tmp/codecov.sh -Z -X fix -X s3 -f coverage-travis.xml "$@" && break
done
