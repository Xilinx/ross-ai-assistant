# Epsilon Extensible Platform: the redesign dropped every option it used to carry.
proc getSupportedBoards {} {
  return [get_board_parts -latest_file_version -quiet -filter {BOARD_NAME=~"*vck190*"}]
}
