import gzip,json,time,io
from pathlib import Path
import re,struct,zlib
from thesistally.discovery_guard import confined
from thesistally.discovery_registry import digest,encoded
from thesistally.fundamental_rendered_build import render_png
from thesistally.fundamental_media import archive_documents
from thesistally.fundamental_legacy_raster import observe
B=Path(__file__).absolute().parent
R=B.parents[1]/'nse'
ONE=R.parent/'ocr-build/page-verifier-24c812e00bf16f3b'
MULTI=B/'recognize-many'
plan=json.loads(gzip.decompress((R/'legacy-optical-plans/2bd57590bc415a08ecd40b217b7c67c4c72c405ff370d0bda0067967e12cf254.json.gz').read_bytes()))
def stamp():return time.monotonic()
def run(cmd,stdin=b''):return confined(cmd,B,stdin=stdin).stdout
def pixels(png):
    width,height=struct.unpack(">II",png[16:24])
    pixels=run(["ffmpeg","-v","error","-i","pipe:0","-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],png)
    if len(pixels)!=width*height*3:raise ValueError("Unexpected raster byte count")
    return width,height,digest(pixels)
def ppm_as_png(raw):
    header=re.match(rb"^P6\s+(\d+)\s+(\d+)\s+255\s",raw);w,h=map(int,header.groups());rgb=raw[header.end():]
    if len(rgb)!=w*h*3:raise ValueError("Unexpected PPM sample length")
    def chunk(k,v):
        return struct.pack(">I",len(v))+k+v+struct.pack(">I",zlib.crc32(k+v)&0xffffffff)
    data=b"".join(b"\0"+rgb[i:i+w*3] for i in range(0,len(rgb),w*3))
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",w,h,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(data,1))+chunk(b"IEND",b"")
def compare(pdf,doc,filings,method,index):
    dest=B/(str(index)+'-'+method); dest.mkdir(exist_ok=True)
    start=stamp()
    if method=='individual':
        pngs=[render_png(R,pdf,str(i),'2400') for i in (1,2)]
    else:
        args=['pdftoppm','-q','-f','1','-l','2','-scale-to','2400']
        if method=='batch_png':args+=['-png']
        run([*args,'-',str(dest/'p')],pdf)
        if method=='batch_png':
            pngs=[p.read_bytes() for p in sorted(dest.glob('p-*.png'))]
        else:
            pngs=[]
            for p in sorted(dest.glob('p-*.ppm')):
                pngs.append(ppm_as_png(p.read_bytes()))
    raster_s=stamp()-start
    if len(pngs)!=2:raise ValueError('Expected two original pages')
    files=[]
    for i,png in enumerate(pngs,1):
        f=dest/f'input{i}.png';f.write_bytes(png);files.append(str(f))
    start=stamp()
    if method=='individual':
        boxes=[json.loads(run([str(ONE)],png)) for png in pngs]
    else:
        boxes=[json.loads(v) for v in run([str(MULTI),*files]).splitlines()]
    ocr_s=stamp()-start
    if len(boxes)!=2:raise ValueError('Expected two optical outputs')
    return {'mode':method,'pdf_sha256':digest(pdf),'original_pages':[1,2],
          'render_s':raster_s,'ocr_s':ocr_s,'total_s':raster_s+ocr_s,'word_counts':[len(v) for v in boxes],
          'pixel_digests':[pixels(v) for v in pngs],'png_sha256':[digest(v) for v in pngs],
          'word_boxes_sha256':[digest(encoded(v)) for v in boxes],
          'field_decisions_sha256':[digest(encoded([observe(f,row,image_bytes=png) for f in filings])) for row,png in zip(boxes,pngs)]}
rows=[]
for i,order in ((1050,['individual','batch_png','batch_ppm']), (1815,['batch_png','batch_ppm','individual']), (2777,['batch_ppm','individual','batch_png'])):
    item=plan['tasks'][i];package=json.loads(gzip.decompress((R/item[1]).read_bytes()))
    sha=package['sha256']; source=gzip.decompress((R/'notice-objects'/sha[:2]/(sha+'.gz')).read_bytes());assert digest(source)==sha
    selected=None
    for meta,pdf in archive_documents(source,root=R):
        pages=next(x['pages'] for x in package['pdfs'] if x['pdf_sha256']==meta['pdf_sha256']);n=len(pages)-(len(pages)>0 and not pages[-1].strip())
        if n>=2:selected=meta,pdf;break
    if not selected:raise ValueError('Sample requires two original pages')
    for method in order:
        result=compare(selected[1],selected[0],item[3],method,i);print(json.dumps(result),flush=True);rows.append(result)
(B/'timings.json').write_text(json.dumps(rows,indent=2)+'\n')
