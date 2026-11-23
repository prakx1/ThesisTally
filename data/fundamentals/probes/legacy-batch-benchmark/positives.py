import gzip,json,tempfile,subprocess,struct
from pathlib import Path
from time import monotonic
from thesistally.discovery_registry import digest,encoded
from thesistally.discovery_guard import confined
from thesistally.fundamental_rendered_money import observe
from thesistally.fundamental_rendered_build import original_pdf
from thesistally.fundamental_legacy_batch import program,render_pages,recognize_pages,profile
B=Path(__file__).absolute().parent; root=B.parents[1]/'nse'
binary=program(root)
def decode(raw):return digest(confined(['ffmpeg','-v','error','-i','pipe:0','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],B,stdin=raw).stdout)
from thesistally.fundamental_nse import load_plans
sourceplans={(r["isin"],r["period_end"],r["basis"]):r for r in load_plans(root)}
selected={}
for f in sorted((root/'notice-rendered-money/v2').rglob('*.json')):
 e=json.load(open(f));info=e['identity']
 field=info['field']
 info={**sourceplans.get((info['isin'],info['period_end'],info['basis']),{}),**info}
 if field not in ('eps','revenue') or field in selected:continue
 sample=next((s for s in e['samples'] if s['size']==2400 and s['observation']),None)
 if not sample:continue
 boxes=json.loads(gzip.decompress((root/'rendered-page-objects'/sample['boxes_sha256'][:2]/(sample['boxes_sha256']+'.json.gz')).read_bytes()))
 if observe(info,boxes,field)!=sample['observation']:continue
 selected[field]=f,e,info,sample
if len(selected)!=2:raise ValueError('Missing two existing positive case types')
images=[];anchors=[];rows=[]
with tempfile.TemporaryDirectory(dir=B) as tmp:
 for field,(f,e,info,sample) in selected.items():
  pdf=original_pdf(root,info['source_sha256'],info)
  child=Path(tmp)/field;child.mkdir()
  t=monotonic();img,sha=render_pages(child,pdf,info['page'],info['page'])[0];elapsed=monotonic()-t
  original=(root/'rendered-page-objects'/sample['rendered_page_sha256'][:2]/(sample['rendered_page_sha256']+'.png')).read_bytes()
  assert decode(img)==sha==decode(original)
  images.append((img,sha));anchors.append((field,f,e,info,sample,digest(img),sha,elapsed))
 t=monotonic(); boxes=recognize_pages(Path(tmp),binary,images);ocr=monotonic()-t
 for raw,(field,f,e,info,sample,psha,sha,elapsed) in zip(boxes,anchors):
  assert digest(raw)==sample['boxes_sha256']
  obs=observe(info,json.loads(raw),field);assert obs==sample['observation']
  rows.append(dict(field=field,receipt=str(f.relative_to(root)),original_pdf=info['pdf_sha256'],page=info['page'],
      period_end=info['period_end'],what_matched=obs['value_inr'],rgb_sha256=sha,old_png_sha256=sample['rendered_page_sha256'],
      produced_png_sha256=psha,boxes_sha256=digest(raw),render_s=elapsed))
(B/'confirmed-positive-cells.json').write_text(json.dumps(dict(profile=profile(),checks=rows,batch_ocr_s=ocr),indent=2)+'\n')
print(json.dumps(rows),flush=True)
