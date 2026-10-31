from pathlib import Path
import hashlib
import os
import shutil
import urllib.request
import zipfile

MODEL_NAME='vosk-model-small-es-0.42'
MODEL_URL='https://alphacephei.com/vosk/models/'+MODEL_NAME+'.zip'
# Hash del ZIP oficial usado al validar esta versión.
MODEL_SHA256='09b239888f633ef2f0b4e09736e3d9936acfd810bc65d53fad45261762c6511f'

def cache_directory()->Path:
    parent=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.cache')))
    return parent/'MochiVoz'/'modelos'

def ensure_model(report,stop,override=None)->Path:
    target=Path(override) if override else cache_directory()/MODEL_NAME
    if (target/'am/final.mdl').is_file() and (target/'conf/model.conf').is_file():
        return target
    if override: raise RuntimeError('La carpeta del modelo no contiene am/final.mdl y conf/model.conf.')
    parent=target.parent; parent.mkdir(parents=True,exist_ok=True)
    archive=parent/(MODEL_NAME+'.part')
    temp=parent/(MODEL_NAME+'.extraer')
    report('Descargando modelo español (39 MB, una sola vez)…')
    digest=hashlib.sha256(); total=0; last_report=-1
    try:
        with urllib.request.urlopen(MODEL_URL,timeout=40) as source,archive.open('wb') as out:
            expected=int(source.headers.get('Content-Length','0'))
            while True:
                if stop.is_set(): raise InterruptedError('Conexión cancelada.')
                block=source.read(256*1024)
                if not block: break
                out.write(block); digest.update(block); total+=len(block)
                progress=round(total*100/expected) if expected else total//1048576
                if progress!=last_report:
                    report(f'Descargando modelo español: {progress}{" %" if expected else " MB"}')
                    last_report=progress
        if digest.hexdigest()!=MODEL_SHA256:
            raise RuntimeError('El modelo descargado no coincide con la versión validada. Vuelve a intentar la descarga.')
        report('Preparando el modelo español…')
        if temp.exists(): shutil.rmtree(temp)
        temp.mkdir()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if stop.is_set(): raise InterruptedError('Conexión cancelada.')
                path=(temp/info.filename).resolve()
                if not path.is_relative_to(temp.resolve()): raise RuntimeError('ZIP con una ruta inválida.')
            z.extractall(temp)
        extracted=temp/MODEL_NAME
        if not (extracted/'am/final.mdl').is_file(): raise RuntimeError('Modelo incompleto.')
        if target.exists(): shutil.rmtree(target)
        extracted.rename(target)
        return target
    finally:
        archive.unlink(missing_ok=True)
        if temp.exists(): shutil.rmtree(temp)
