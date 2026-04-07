it/elements/widgets/text_widgets.py", line 695, in text_area
    return self._text_area(
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/elements/widgets/text_widgets.py", line 740, in _text_area
    maybe_raise_label_warnings(label, label_visibility)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/elements/lib/policies.py", line 187, in maybe_raise_label_warnings
    _LOGGER.warning(
^C  Stopping...
(.venv) sivakue-in-la1:copy sivakue$ clear
(.venv) sivakue-in-la1:copy sivakue$ streamlit run app.py

  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://192.168.1.3:8501

  For better performance, install the Watchdog module:

  $ xcode-select --install
  $ pip install watchdog
            
2026-04-07 21:29:04.950 `label` got an empty value. This is discouraged for accessibility reasons and may be disallowed in the future by raising an exception. Please provide a non-empty label and hide it with label_visibility if needed.
Stack (most recent call last):
  File "/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/threading.py", line 1014, in _bootstrap
    self._bootstrap_inner()
  File "/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/threading.py", line 1043, in _bootstrap_inner
    self.run()
  File "/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/threading.py", line 994, in run
    self._target(*self._args, **self._kwargs)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/runtime/scriptrunner/script_runner.py", line 379, in _run_script_thread
    self._run_script(request.rerun_data)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/runtime/scriptrunner/script_runner.py", line 705, in _run_script
    ) = exec_func_with_error_handling(code_to_exec, ctx)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/runtime/scriptrunner/exec_code.py", line 129, in exec_func_with_error_handling
    result = func()
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/runtime/scriptrunner/script_runner.py", line 689, in code_to_exec
    exec(code, module.__dict__)  # noqa: S102
  File "/Users/sivakue/Desktop/personal/copy/app.py", line 604, in <module>
    st.text_area("", transcribed_text, height=400)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/runtime/metrics_util.py", line 563, in wrapped_func
    result = non_optional_func(*args, **kwargs)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/elements/widgets/text_widgets.py", line 695, in text_area
    return self._text_area(
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/elements/widgets/text_widgets.py", line 740, in _text_area
    maybe_raise_label_warnings(label, label_visibility)
  File "/Users/sivakue/Desktop/personal/copy/.venv/lib/python3.13/site-packages/streamlit/elements/lib/policies.py", line 187, in maybe_raise_label_warnings
    _LOGGER.warning(
