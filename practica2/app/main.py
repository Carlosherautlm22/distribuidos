import os,logging
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
mode=os.environ.get('MODE','api')
if mode=='bootstrap':
    from .bootstrap import run
    run()
else:
    from .common import init_local
    init_local()
    if mode=='api':
        from .api import app
        app.run(host='0.0.0.0',port=8000,threaded=True,use_reloader=False)
    elif mode=='monitor':
        from .monitor import run
        run()
    elif mode=='consumer':
        from .consumer import run
        run()
    else: raise ValueError(mode)
