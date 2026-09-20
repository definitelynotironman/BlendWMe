"""Run without Blender: python3 -m unittest discover -s tests -v."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]/'extension'
pkg=types.ModuleType('bwm_core');pkg.__path__=[str(ROOT)];sys.modules['bwm_core']=pkg
for n in ('protocol','demo'):
    spec=importlib.util.spec_from_file_location('bwm_core.'+n,ROOT/(n+'.py'))
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
from bwm_core import protocol as p, demo

class ProtocolTests(unittest.TestCase):
    def test_no_code_or_destructive_tools(self):
        for tool in ('exec','delete_object','run_python','__import__'):
            with self.assertRaises(ValueError):p.validate_action({'tool':tool,'args':{'name':'Cube'}})
    def test_reject_malformed_fields(self):
        invalid=[{'location':[1,2]}, {'scale':[0,1,1]}, {'scale':[True,1,1]}, {'location':[float('nan'),0,0]}, {'location':[1001,0,0]}, {'code':'danger'}]
        for args in invalid:
            with self.subTest(args=args),self.assertRaises(ValueError):
                p.validate_action({'tool':'create_primitive','args':{'name':'x','primitive':'cube',**args}})
    def test_reject_nonboolean_done_and_extra_fields(self):
        for raw in ({'message':'x','done':'false'}, {'message':'x','done':False,'code':'exec'}, {'message':'x','done':False,'guide':{'target':'SCREEN_XY'}}):
            with self.assertRaises(ValueError):p.parse_response(raw)
    def test_reject_completion_with_action(self):
        with self.assertRaises(ValueError):p.parse_response({'message':'done','done':True,'action':{'tool':'add_bevel','args':{'name':'x'}}})
    def test_fenced_json(self):
        self.assertTrue(p.parse_response('```json\n{"message":"ok","done":true,"action":null}\n```')['done'])
    def test_selection_does_not_invalidate_scene(self):
        a={'objects':[{'name':'x','scale':[1,1,1],'selected':False}]}
        b={'objects':[{'name':'x','scale':[1,1,1],'selected':True}]}
        self.assertEqual(p.signature(a),p.signature(b));b['objects'][0]['scale']=[2,1,1];self.assertNotEqual(p.signature(a),p.signature(b))
    def test_recipe_rejects_unknown_prompt(self):
        r=demo.next_demo({'instruction':'make an intricate dragon','history':[],'scene':{'objects':[]},'prefix':'BWM_'})
        self.assertIsNone(r['action']);self.assertIn('not a language model',r['message'])
    def test_live_base_drives_stem_and_renames(self):
        hist=[{'action':{'tool':'create_primitive','args':{'name':'BWM_Base'}},'object_id':'base'}]
        hist+= [{'action':{'tool':'set_material','args':{'name':'BWM_Base'}}},{'action':{'tool':'add_bevel','args':{'name':'BWM_Base'}}}]
        r=demo.next_demo({'instruction':'lamp','history':hist,'scene':{'objects':[{'id':'base','name':'My Base','location':[4,2,.5],'dimensions':[3,3,1]}]},'prefix':'BWM_'})
        self.assertEqual(r['action']['args']['location'],[4,2,2.25])
    def test_material_and_bevel_ranges(self):
        for args in ({'name':'x','width':-1},{'name':'x','segments':3.5},{'name':'x','segments':True}):
            with self.assertRaises(ValueError):p.validate_action({'tool':'add_bevel','args':args})
        with self.assertRaises(ValueError):p.validate_action({'tool':'set_material','args':{'name':'x','base_color':[1,1,2,1]}})

if __name__=='__main__':unittest.main()
