"""Render mip-zero RGBA sampling under 8:1 minification in Cycles and Eevee.

Run: blender --background --factory-startup --python-exit-code 1 --python scripts/verify_frontier_mip0.py

The shader renders absolute error against analytic interpolation of an
alternating source row, so antialiasing cannot hide sampling differences.
Use power-of-two periods to avoid GPU modulo rounding at integer boundaries.
The original float texels and independent alpha must survive GPU sampling.
"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import bpy
from carbon_eve_resources.quad.frontier import Graph
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
mat=bpy.data.materials.new('Mip0 minification error');mat.use_nodes=True;mat.node_tree.nodes.clear()
g=Graph(mat.node_tree,None,False);m=g.math
uv=g.nodes.new('ShaderNodeTexCoord')
sep=g.nodes.new('ShaderNodeSeparateXYZ');g.bind(uv.outputs['UV'],sep.inputs[0])
selector=sep.outputs['X']
samples=tuple((i%2,1-i%2,(i%4)*.25,.25+.5*(i%2)) for i in range(256))
rgb,alpha=g.lookup_rgba_mip0(samples,selector)
x=m('MINIMUM',m('MAXIMUM',m('SUBTRACT',m('MULTIPLY',selector,256),.5),0),255)
a=m('FLOOR',x);b=m('MINIMUM',m('ADD',a,1),255);f=m('SUBTRACT',x,a)
def endpoint(i):
    r=m('MODULO',i,2)
    return (r,m('SUBTRACT',1,r),m('MULTIPLY',m('MODULO',i,4),.25),m('ADD',.25,m('MULTIPLY',.5,r)))
expected=[m('ADD',m('MULTIPLY',aa,m('SUBTRACT',1,f)),m('MULTIPLY',bb,f)) for aa,bb in zip(endpoint(a),endpoint(b))]
error=g.vector('ABSOLUTE',g.vector('SUBTRACT',rgb,g.combine(*expected[:3])))
alpha_error=m('ABSOLUTE',m('SUBTRACT',alpha,expected[3]))
error=g.vector('ADD',error,g.combine(alpha_error,alpha_error,alpha_error))
emit=g.nodes.new('ShaderNodeEmission');g.bind(error,emit.inputs['Color'])
out=g.nodes.new('ShaderNodeOutputMaterial');g.bind(emit.outputs[0],out.inputs['Surface'])
bpy.ops.mesh.primitive_plane_add(size=2);bpy.context.object.data.materials.append(mat)
bpy.ops.object.camera_add(location=(0,0,3));scene=bpy.context.scene;scene.camera=bpy.context.object
scene.camera.data.type='ORTHO';scene.camera.data.ortho_scale=2
scene.render.resolution_x=scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32'
scene.world.color=(0,0,0)
output = tempfile.TemporaryDirectory(prefix='carbon-mip0-')
for engine in ('CYCLES','BLENDER_EEVEE'):
    scene.render.engine=engine
    if engine=='CYCLES':scene.cycles.samples=4;scene.cycles.use_denoising=False
    scene.render.filepath=str(Path(output.name) / ('heat-mip0-'+engine+'.exr'))
    bpy.ops.render.render(write_still=True)
    image=bpy.data.images.load(scene.render.filepath,check_existing=False);pixels=image.pixels[:]
    error=max(pixels[(y*32+x)*4+c] for y in range(4,28) for x in range(4,28) for c in range(3))
    print('MIP0_MINIFICATION_ERROR',engine,error,flush=True)
    assert error<1e-5


output.cleanup()

