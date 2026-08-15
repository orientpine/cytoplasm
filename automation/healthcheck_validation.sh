#!/usr/bin/env bash

valid_account() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]*$ ]]
}

valid_http_url() {
  local pattern='^https?://[a-zA-Z0-9._:-]+/[a-zA-Z0-9._~:/?&=%+-]*$'
  [[ "$1" =~ $pattern ]]
}

valid_unit() {
  [[ "$1" =~ ^[a-zA-Z0-9_.@-]+$ ]]
}

valid_abs_path() {
  [[ "$1" =~ ^/[A-Za-z0-9._/-]*$ ]]
}
