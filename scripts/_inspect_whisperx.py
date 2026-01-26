import inspect
import whisperx

print("whisperx_file=", getattr(whisperx, "__file__", ""))
print("load_model_sig=", inspect.signature(whisperx.load_model))
try:
    import whisperx.vads as vads

    print("vads_file=", getattr(vads, "__file__", ""))
except Exception as e:
    print("vads_import_error=", repr(e))

