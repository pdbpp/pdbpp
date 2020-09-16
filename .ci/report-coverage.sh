#!/bin/sh

set -ex

# Set --connect-timeout to work around https://github.com/curl/curl/issues/4461
curl -S -L --connect-timeout 5 --retry 6 -s -o /tmp/codecov.sh https://codecov.io/bash

for _ in 1 2 3 4 5; do
  bash /tmp/codecov.sh -Z -X fix -X s3 -f coverage.xml "$@" && break
done
