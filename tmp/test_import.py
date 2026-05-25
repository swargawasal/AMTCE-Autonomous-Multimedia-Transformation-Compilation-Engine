try:
    import compiler
    print("Successfully imported compiler")
    print("check_health:", hasattr(compiler, 'check_health'))
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Error: {e}")
