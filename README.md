Use additional command line flag `-ftime-trace` then building your code with
Clang 9. Compiler will produce `.json` files next to object files. To view
profiling result for single file open `chrome://tracing/` URL in Google Chrome
(or Chromium).

If you want to see which include file, class or function takes most of frontend
time across all of your sources, run following commang:  
`python3 clang_combined_time_trace.py <path to directory with traces> <output>`

Script will try to load all `.json` files in specified directory as if they are
Clang traces and generate text file with summary info.
