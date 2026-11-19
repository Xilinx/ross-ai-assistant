// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <adf.h>
#include "graph.h"

simpleStream ss;

int main(void){
   
    ss.init();
    ss.run(1);
    //ss.wait(2);
    ss.end();


    return 0;
}
