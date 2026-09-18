"""Explicit opt-in GGUF downloads, pinned revisions and SHA-256 verification."""
import hashlib
from pathlib import Path
import sys
import urllib.request
CATALOG={
 'gemma4':('unsloth/gemma-4-E4B-it-GGUF','bfc15c382204943c3a8fff0c750b94ae2364d7a3','gemma-4-E4B-it-Q4_K_M.gguf','85a896a047553e842f25297ee5b031d64ff30147d9c4af17b1e4b394cd1fab87'),
 'qwen36':('unsloth/Qwen3.6-35B-A3B-GGUF','a483e9e6cbd595906af30beda3187c2663a1118c','Qwen3.6-35B-A3B-UD-Q3_K_M.gguf','1b715841683f960bd9a49f008181bd910ee169b78d4cf465b6fde7f4d929ff99')}
def download(model,folder):
 repo,revision,name,expected=CATALOG[model];folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
 target=folder/name;temp=target.with_suffix('.partial')
 if target.exists():raise ValueError('Arquivo já existe; não será sobrescrito.')
 digest=hashlib.sha256();size=0;last=0
 try:
  with urllib.request.urlopen(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',timeout=60) as response,temp.open('xb') as out:
   while chunk:=response.read(4*1024*1024):
    out.write(chunk);digest.update(chunk);size+=len(chunk)
    if size-last>=128*1024**2:print(f'Baixados {size//1024**2} MiB',flush=True);last=size
  if digest.hexdigest()!=expected:raise ValueError('SHA-256 divergente; arquivo rejeitado.')
  temp.replace(target);print('Download verificado: '+str(target),flush=True)
 finally:
  if temp.exists():temp.unlink()
if __name__=='__main__':download(sys.argv[1],sys.argv[2])
