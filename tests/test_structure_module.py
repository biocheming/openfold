# Copyright 2021 AlQuraishi Laboratory
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch
import numpy as np
import unittest

from openfold.np.residue_constants import (
    restype_rigid_group_default_frame,
    restype_atom14_to_rigid_group,
    restype_atom14_mask,
    restype_atom14_rigid_group_positions,
)    
from openfold.model.structure_module import *
from openfold.model.structure_module import (
    _torsion_angles_to_frames,
    _frames_and_literature_positions_to_atom14_pos,
)
from openfold.utils.affine_utils import T
import tests.compare_utils as compare_utils
from tests.config import consts
from tests.data_utils import (
    random_affines_4x4,
)

if(compare_utils.alphafold_is_installed()):
    alphafold = compare_utils.import_alphafold()
    import jax
    import haiku as hk


class TestStructureModule(unittest.TestCase):
    def test_structure_module_shape(self):
        batch_size = 2
        n = 5
        c_s = 7
        c_z = 11
        c_ipa = 13
        c_resnet = 17
        no_heads_ipa = 6
        no_query_points = 4
        no_value_points = 4
        dropout_rate = 0.1
        no_layers = 3
        no_transition_layers = 3
        no_resnet_layers = 3
        ar_epsilon = 1e-6
        no_angles = 7
        trans_scale_factor = 10
        inf = 1e5

        sm = StructureModule(
            c_s,
            c_z,
            c_ipa,
            c_resnet,
            no_heads_ipa,
            no_query_points,
            no_value_points,
            dropout_rate,
            no_layers,
            no_transition_layers,
            no_resnet_layers,
            no_angles,
            trans_scale_factor,
            ar_epsilon,
            inf,
        )

        s = torch.rand((batch_size, n, c_s))
        z = torch.rand((batch_size, n, n, c_z))
        f = torch.randint(low=0, high=21, size=(batch_size, n)).long()

        out = sm(s, z, f)

        self.assertTrue(
            out["frames"].shape == (no_layers, batch_size, n, 4, 4)
        )
        self.assertTrue(
            out["angles"].shape == (no_layers, batch_size, n, no_angles, 2)
        )
        self.assertTrue(
            out["positions"].shape == (no_layers, batch_size, n, 14, 3)
        )

    def test_torsion_angles_to_frames_shape(self):
        batch_size = 2
        n = 5
        rots = torch.rand((batch_size, n, 3, 3))
        trans = torch.rand((batch_size, n, 3))
        ts = T(rots, trans)

        angles = torch.rand((batch_size, n, 7, 2))

        aas = torch.tensor([i % 2 for i in range(n)])
        aas = torch.stack([aas for _ in range(batch_size)])

        frames = _torsion_angles_to_frames(
            ts, 
            angles, 
            aas, 
            torch.tensor(restype_rigid_group_default_frame),
        )

        self.assertTrue(frames.shape == (batch_size, n, 8))

    def test_frames_and_literature_positions_to_atom14_pos_shape(self):
        batch_size = 2
        n = 5
        rots = torch.rand((batch_size, n, 8, 3, 3))
        trans = torch.rand((batch_size, n, 8, 3))
        ts = T(rots, trans)

        f = torch.randint(low=0, high=21, size=(batch_size, n)).long()

        xyz = _frames_and_literature_positions_to_atom14_pos(
            ts,
            f,
            torch.tensor(restype_rigid_group_default_frame),
            torch.tensor(restype_atom14_to_rigid_group),
            torch.tensor(restype_atom14_mask),
            torch.tensor(restype_atom14_rigid_group_positions),
        )
        
        self.assertTrue(xyz.shape == (batch_size, n, 14, 3))

    def test_structure_module_transition_shape(self):
        batch_size = 2
        n = 5
        c = 7
        num_layers = 3
        dropout = 0.1

        smt = StructureModuleTransition(c, num_layers, dropout)

        s = torch.rand((batch_size, n, c))

        shape_before = s.shape
        s = smt(s)
        shape_after = s.shape

        self.assertTrue(shape_before == shape_after)


class TestBackboneUpdate(unittest.TestCase):
    def test_shape(self): 
        batch_size = 2
        n_res = 3
        c_in = 5
        
        bu = BackboneUpdate(c_in)

        s = torch.rand((batch_size, n_res, c_in))

        t = bu(s)
        rot, tra = t.rots, t.trans

        self.assertTrue(rot.shape == (batch_size, n_res, 3, 3))
        self.assertTrue(tra.shape == (batch_size, n_res, 3))


class TestInvariantPointAttention(unittest.TestCase):
    def test_shape(self):
        c_m = 13
        c_z = 17
        c_hidden = 19
        no_heads = 5
        no_qp = 7
        no_vp = 11

        batch_size = 2
        n_res = 23

        s = torch.rand((batch_size, n_res, c_m))
        z = torch.rand((batch_size, n_res, n_res, c_z))
        mask = torch.ones((batch_size, n_res))

        rots = torch.rand((batch_size, n_res, 3, 3))
        trans = torch.rand((batch_size, n_res, 3))

        t = T(rots, trans)

        ipa = InvariantPointAttention(
            c_m, c_z, c_hidden, no_heads, no_qp, no_vp
        )

        shape_before = s.shape
        s = ipa(s, z, t, mask)

        self.assertTrue(s.shape == shape_before)

    @compare_utils.skip_unless_alphafold_installed()
    def test_ipa_compare(self):
        def run_ipa(act, static_feat_2d, mask, affine):
            config = compare_utils.get_alphafold_config() 
            ipa = alphafold.model.folding.InvariantPointAttention(
                config.model.heads.structure_module, 
                config.model.global_config, 
            )
            attn = ipa(
                inputs_1d=act, 
                inputs_2d=static_feat_2d, 
                mask=mask, 
                affine=affine
            )
            return attn
        
        f = hk.transform(run_ipa)
        
        n_res = consts.n_res
        c_s = consts.c_s
        c_z = consts.c_z
        
        sample_act = np.random.rand(n_res, c_s)
        sample_2d = np.random.rand(n_res, n_res, c_z)
        sample_mask = np.ones((n_res, 1))

        affines = random_affines_4x4((n_res,))
        rigids = alphafold.model.r3.rigids_from_tensor4x4(affines)
        quats = alphafold.model.r3.rigids_to_quataffine(rigids)
        transformations = T.from_4x4(
            torch.as_tensor(affines).float().cuda()
        )
        
        sample_affine = quats
        
        ipa_params = compare_utils.fetch_alphafold_module_weights(
            "alphafold/alphafold_iteration/structure_module/" +
            "fold_iteration/invariant_point_attention"
        )

        out_gt = f.apply(
            ipa_params, None, sample_act, sample_2d, sample_mask, sample_affine
        ).block_until_ready()
        out_gt = torch.as_tensor(np.array(out_gt))

        with torch.no_grad():
            model = compare_utils.get_global_pretrained_openfold()
            out_repro = model.structure_module.ipa(
                torch.as_tensor(sample_act).float().cuda(), 
                torch.as_tensor(sample_2d).float().cuda(), 
                transformations, 
                torch.as_tensor(sample_mask.squeeze(-1)).float().cuda(),
            ).cpu()
   
        self.assertTrue(torch.max(torch.abs(out_gt - out_repro)) < consts.eps)


class TestAngleResnet(unittest.TestCase):
    def test_shape(self): 
        batch_size = 2
        n = 3
        c_s = 13
        c_hidden = 11
        no_layers = 5
        no_angles = 7
        epsilon = 1e-12
        
        ar = AngleResnet(c_s, c_hidden, no_layers, no_angles, epsilon)
        a = torch.rand((batch_size, n, c_s))
        a_initial = torch.rand((batch_size, n, c_s))

        _, a = ar(a, a_initial)

        self.assertTrue(a.shape == (batch_size, n, no_angles, 2))


if __name__ == "__main__":
    unittest.main()
