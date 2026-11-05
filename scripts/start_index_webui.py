"""Run the existing official IndexTTS 2.5 UI with local models only."""
import argparse
import os
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--models', required=True)
    parser.add_argument('--port', type=int, default=7860)
    args = parser.parse_args()
    runtime = Path(args.runtime).resolve()
    project = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project))
    from novel_studio.engines import validate_index_model
    validate_index_model(args.models)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
    os.environ['NO_PROXY'] = 'localhost,127.0.0.1'
    sys.path.insert(0, str(runtime))
    os.chdir(runtime)
    from tools.i18n import i18n
    i18n.I18N_JSON_DIR = str(runtime / 'tools' / 'i18n' / 'locale')
    from indextts.utils import model_download, examples_downloader

    def deny_download(*args, **kwargs):
        raise RuntimeError('自动下载已禁用，请选择本机已有模型。')

    # The official UI otherwise fetches optional demo samples at startup.
    examples_downloader.ensure_examples_available = lambda *a, **k: None
    model_download.snapshot_download = deny_download
    model_download._download_single_file = deny_download
    model_download.ensure_models_available = deny_download
    data = project / 'index-webui-data'
    (data / 'examples').mkdir(parents=True, exist_ok=True)
    cases = data / 'examples' / 'cases.jsonl'
    if not cases.exists():
        cases.write_text('', encoding='utf-8')
    os.chdir(data)
    sys.argv = [str(runtime / 'webui.py'), '--model_dir', str(Path(args.models).resolve()),
                '--version', '2.5', '--host', '127.0.0.1', '--port', str(args.port)]
    runpy.run_path(str(runtime / 'webui.py'), run_name='__main__')


if __name__ == '__main__':
    main()
