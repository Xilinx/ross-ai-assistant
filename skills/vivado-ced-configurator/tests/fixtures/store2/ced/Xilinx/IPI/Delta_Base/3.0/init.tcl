# Delta Common Platform: no configurable options, identical across revisions.
proc getSupportedBoards {} {
  return [get_board_parts -latest_file_version -quiet -filter {BOARD_NAME=~"*vck190*"}]
}
