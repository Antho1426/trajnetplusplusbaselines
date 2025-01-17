"""Command line tool to train an SGAN model."""

import argparse
import logging
import socket
import sys
import time
import random
import os
import pickle
import copy

import numpy as np

import torch
import trajnetplusplustools

from .. import augmentation
from ..lstm.loss import PredictionLoss, L2Loss
from ..lstm.loss import gan_d_loss, gan_g_loss # variety_loss
from ..lstm.gridbased_pooling import GridBasedPooling
from ..lstm.non_gridbased_pooling import NN_Pooling, HiddenStateMLPPooling, AttentionMLPPooling, DirectionalMLPPooling
from ..lstm.non_gridbased_pooling import NN_LSTM, TrajectronPooling, SAttention_fast
from ..lstm.more_non_gridbased_pooling import NMMP
from .sgan import SGAN, drop_distant, SGANPredictor
from .sgan import LSTMGenerator, LSTMDiscriminator
from .. import __version__ as VERSION

from ..lstm.utils import center_scene, random_rotation
from ..lstm.data_load_utils import prepare_data
from torch import nn as nn


#
class ProjHead(nn.Module):
    """
        Nonlinear projection head that maps the extracted motion features to the embedding space
    """

    def __init__(self, feat_dim, hidden_dim, head_dim):
        super(ProjHead, self).__init__()
        self.head = nn.Sequential(
            nn.Linear(feat_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim, head_dim)
        )

    def forward(self, feat):
        return self.head(feat)

class SpatialEncoder(nn.Module):
    """
        Spatial encoder that maps a sampled location to the embedding space
    """

    def __init__(self, hidden_dim, head_dim):
        super(SpatialEncoder, self).__init__()
        self.encoder = nn.Sequential(
            nn.Linear(2, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim, head_dim)
        )

    def forward(self, state):
        return self.encoder(state)

class Trainer(object):
    def __init__(self, model=None, g_optimizer=None, g_lr_scheduler=None, d_optimizer=None, d_lr_scheduler=None,
                 criterion=None, device=None, batch_size=8, obs_length=9, pred_length=12, augment=True,
                 normalize_scene=False, save_every=1, start_length=0, val_flag=True):
        self.model = model if model is not None else SGAN()
        self.g_optimizer = g_optimizer if g_optimizer is not None else torch.optim.Adam(
                           model.generator.parameters(), lr=1e-3, weight_decay=1e-4)
        self.d_optimizer = d_optimizer if d_optimizer is not None else torch.optim.Adam(
                           model.discriminator.parameters(), lr=1e-3, weight_decay=1e-4)
        self.g_lr_scheduler = g_lr_scheduler if g_lr_scheduler is not None else \
                              torch.optim.lr_scheduler.StepLR(g_optimizer, 10)
        self.d_lr_scheduler = d_lr_scheduler if d_lr_scheduler is not None else \
                              torch.optim.lr_scheduler.StepLR(d_optimizer, 10)

        self.criterion = criterion if criterion is not None else PredictionLoss(keep_batch_dim=True)
        self.device = device if device is not None else torch.device('cpu')
        self.model = self.model.to(self.device)
        self.criterion = self.criterion.to(self.device)
        self.criterion_contrast = nn.CrossEntropyLoss()
        self.log = logging.getLogger(self.__class__.__name__)
        self.save_every = save_every

        self.batch_size = batch_size
        self.obs_length = obs_length
        self.pred_length = pred_length
        self.seq_length = self.obs_length+self.pred_length
        self.start_length = start_length

        self.augment = augment
        self.normalize_scene = normalize_scene

        self.val_flag = val_flag

    def loop(self, train_scenes, val_scenes, train_goals, val_goals, out, epochs=35, start_epoch=0):
        for epoch in range(start_epoch, epochs):
            if epoch % self.save_every == 0:
                state = {'epoch': epoch, 'state_dict': self.model.state_dict(),
                         'g_optimizer': self.g_optimizer.state_dict(), 'd_optimizer': self.d_optimizer.state_dict(),
                         'g_lr_scheduler': self.g_lr_scheduler.state_dict(),
                         'd_lr_scheduler': self.d_lr_scheduler.state_dict()}
                SGANPredictor(self.model).save(state, out + '.epoch{}'.format(epoch))
            self.train(train_scenes, train_goals, epoch)
            if self.val_flag:
                self.val(val_scenes, val_goals, epoch)

        state = {'epoch': epoch + 1, 'state_dict': self.model.state_dict(),
                 'g_optimizer': self.g_optimizer.state_dict(), 'd_optimizer': self.d_optimizer.state_dict(),
                 'g_lr_scheduler': self.g_lr_scheduler.state_dict(),
                 'd_lr_scheduler': self.d_lr_scheduler.state_dict()}
        SGANPredictor(self.model).save(state, out + '.epoch{}'.format(epoch + 1))
        SGANPredictor(self.model).save(state, out)

    def get_lr(self):
        for param_group in self.g_optimizer.param_groups:
            return param_group['lr']

    def train(self, scenes, goals, epoch):
        start_time = time.time()

        print('epoch', epoch)

        random.shuffle(scenes)
        epoch_loss = 0.0
        self.model.train()
        self.g_optimizer.zero_grad()
        self.d_optimizer.zero_grad()

        ## Initialize batch of scenes
        batch_scene = []
        batch_scene_goal = []
        batch_split = [0]

        d_steps_left = self.model.d_steps
        g_steps_left = self.model.g_steps
        for scene_i, (filename, scene_id, paths) in enumerate(scenes):
            scene_start = time.time()

            ## make new scene
            scene = trajnetplusplustools.Reader.paths_to_xy(paths)

            ## get goals
            if goals is not None:
                scene_goal = np.array(goals[filename][scene_id])
            else:
                scene_goal = np.array([[0, 0] for path in paths])

            ## Drop Distant
            scene, mask = drop_distant(scene)
            scene_goal = scene_goal[mask]

            ##process scene
            if self.normalize_scene:
                scene, _, _, scene_goal = center_scene(scene, self.obs_length, goals=scene_goal)
            if self.augment:
                scene, scene_goal = random_rotation(scene, goals=scene_goal)
            
            ## Augment scene to batch of scenes
            batch_scene.append(scene)
            batch_split.append(int(scene.shape[1]))
            batch_scene_goal.append(scene_goal)

            if ((scene_i + 1) % self.batch_size == 0) or ((scene_i + 1) == len(scenes)):
                ## Construct Batch
                batch_scene = np.concatenate(batch_scene, axis=1)
                batch_scene_goal = np.concatenate(batch_scene_goal, axis=0)
                batch_split = np.cumsum(batch_split)
                
                batch_scene = torch.Tensor(batch_scene).to(self.device)
                batch_scene_goal = torch.Tensor(batch_scene_goal).to(self.device)
                batch_split = torch.Tensor(batch_split).to(self.device).long()

                preprocess_time = time.time() - scene_start

                # Decide whether to use the batch for stepping on discriminator or
                # generator; an iteration consists of args.g_steps steps on the
                # generator followed by args.d_steps steps on the discriminator.
                if g_steps_left > 0:
                    step_type = 'g'
                    g_steps_left -= 1
                    ## Train Batch
                    loss, contrastLoss = self.train_batch(batch_scene, batch_scene_goal, batch_split, step_type='g')

                elif d_steps_left > 0:
                    step_type = 'd'
                    d_steps_left -= 1
                    ## Train Batch
                    loss, contrastLoss = self.train_batch(batch_scene, batch_scene_goal, batch_split, step_type='d')

                epoch_loss += loss
                total_time = time.time() - scene_start

                ## Reset Batch
                batch_scene = []
                batch_scene_goal = []
                batch_split = [0]

                ## Update d_steps, g_steps once they end
                if d_steps_left == 0 and g_steps_left == 0:
                    d_steps_left = self.model.d_steps
                    g_steps_left = self.model.g_steps

            if (scene_i + 1) % (10*self.batch_size) == 0:
                self.log.info({
                    'type': 'train',
                    'epoch': epoch, 'batch': scene_i, 'n_batches': len(scenes),
                    'time': round(total_time, 3),
                    'data_time': round(preprocess_time, 3),
                    'lr': self.get_lr(),
                    'loss': round(loss, 3),
                    'contrastLoss': round(contrastLoss.item(), 3),
                })

        self.g_lr_scheduler.step()
        self.d_lr_scheduler.step()

        self.log.info({
            'type': 'train-epoch',
            'epoch': epoch + 1,
            'loss': round(epoch_loss / (len(scenes)), 5),
            'time': round(time.time() - start_time, 1),
        })

    def val(self, scenes, goals, epoch):
        eval_start = time.time()

        val_loss = 0.0
        test_loss = 0.0
        self.model.train()  # so that it does not return positions but still normals

        ## Initialize batch of scenes
        batch_scene = []
        batch_scene_goal = []
        batch_split = [0]

        for scene_i, (filename, scene_id, paths) in enumerate(scenes):
            # make new scene
            scene = trajnetplusplustools.Reader.paths_to_xy(paths)

            ## get goals
            if goals is not None:
                # scene_goal = np.array([goals[path[0].pedestrian] for path in paths])
                scene_goal = np.array(goals[filename][scene_id])
            else:
                scene_goal = np.array([[0, 0] for path in paths])

            ## Drop Distant
            scene, mask = drop_distant(scene)
            scene_goal = scene_goal[mask]

            ##process scene
            if self.normalize_scene:
                scene, _, _, scene_goal = center_scene(scene, self.obs_length, goals=scene_goal)

            ## Augment scene to batch of scenes
            batch_scene.append(scene)
            batch_split.append(int(scene.shape[1]))
            batch_scene_goal.append(scene_goal)

            if ((scene_i + 1) % self.batch_size == 0) or ((scene_i + 1) == len(scenes)):
                ## Construct Batch
                batch_scene = np.concatenate(batch_scene, axis=1)
                batch_scene_goal = np.concatenate(batch_scene_goal, axis=0)
                batch_split = np.cumsum(batch_split)
                
                batch_scene = torch.Tensor(batch_scene).to(self.device)
                batch_scene_goal = torch.Tensor(batch_scene_goal).to(self.device)
                batch_split = torch.Tensor(batch_split).to(self.device).long()
                
                loss_val_batch, loss_test_batch = self.val_batch(batch_scene, batch_scene_goal, batch_split)
                val_loss += loss_val_batch
                test_loss += loss_test_batch

                ## Reset Batch
                batch_scene = []
                batch_scene_goal = []
                batch_split = [0]

        eval_time = time.time() - eval_start

        self.log.info({
            'type': 'val-epoch',
            'epoch': epoch + 1,
            'loss': round(val_loss / (len(scenes)), 3),
            'test_loss': round(test_loss / len(scenes), 3),
            'time': round(eval_time, 1),
        })

    def train_batch(self, batch_scene, batch_scene_goal, batch_split, step_type):
        """Training of B batches in parallel, B : batch_size

        Parameters
        ----------
        batch_scene : Tensor [seq_length, num_tracks, 2]
            Tensor of batch of scenes.
        batch_scene_goal : Tensor [num_tracks, 2]
            Tensor of goals of each track in batch
        batch_split : Tensor [batch_size + 1]
            Tensor defining the split of the batch.
            Required to identify the tracks of to the same scene
        step_type : String ('g', 'd')
            Determines whether to train generator or discriminator

        Returns
        -------
        loss : scalar
            Training loss of the batch
        """

        observed = batch_scene[self.start_length:self.obs_length].clone()
        prediction_truth = batch_scene[self.obs_length:].clone()
        targets = batch_scene[self.obs_length:self.seq_length] - batch_scene[self.obs_length-1:self.seq_length-1]

        rel_output_list, outputs, scores_real, scores_fake, batch_feat = self.model(observed, batch_scene_goal, batch_split, prediction_truth,
                                                                        step_type=step_type, pred_length=self.pred_length)

        loss, lossContrast = self.loss_criterion(rel_output_list, targets, batch_split, scores_fake, scores_real, step_type, batch_scene, batch_feat)

        if step_type == 'g':
            self.g_optimizer.zero_grad()
            loss.backward()
            self.g_optimizer.step()

        else:
            self.d_optimizer.zero_grad()
            loss.backward()
            self.d_optimizer.step()

        return loss.item(), lossContrast

    def val_batch(self, batch_scene, batch_scene_goal, batch_split):
        """Validation of B batches in parallel, B : batch_size

        Parameters
        ----------
        batch_scene : Tensor [seq_length, num_tracks, 2]
            Tensor of batch of scenes.
        batch_scene_goal : Tensor [num_tracks, 2]
            Tensor of goals of each track in batch
        batch_split : Tensor [batch_size + 1]
            Tensor defining the split of the batch.
            Required to identify the tracks of to the same scene

        Returns
        -------
        loss : scalar
            Validation loss of the batch when groundtruth of neighbours
            is not provided
        """

        observed = batch_scene[self.start_length:self.obs_length]
        #prediction_truth = batch_scene[self.obs_length:].clone() # CLONE
        targets = batch_scene[self.obs_length:self.seq_length] - batch_scene[self.obs_length-1:self.seq_length-1]
        
        with torch.no_grad():
            # "batch_feat" added as an additional returned argument by Antho
            rel_output_list, _, _, _, batch_feat = self.model(observed, batch_scene_goal, batch_split,
                                                  n_predict=self.pred_length, pred_length=self.pred_length)

            # top-k loss
            loss = self.variety_loss(rel_output_list, targets, batch_split)

        return 0.0, loss.item()



    def _sampling_spatial(self, batch_scene, batch_split):
        self.noise_local = 0.05  # TODO maybe 0.1 or 0.025
        self.min_seperation = 0.45  # TODO increase this ? (uncomfortable zone is up to 45[cm])
        self.max_seperation = 5  # TODO increase this ? (anyway not used for the moment)
        self.agent_zone = self.min_seperation * torch.tensor(
            [[1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0], [0.707, 0.707],
             [0.707, -0.707], [-0.707, 0.707], [-0.707, -0.707], [0.0, 0.0]])

        # The prefix "_" indicates that this is a private function that we can only
        # access from the class
        # batch_split : 9 (ID of the persons we want to select (except the last element which marks the end of the batch))
        # batch_scene : (time x persons x coordinates) --> for instance: 21 x 39 x 2

        # gt_future : (time x person x coord)
        # gt_future = batch_scene[self.obs_length: self.obs_length+self.pred_length]

        # Selecting only the first pred sample (i.e. the prediction for the first timestamp)
        # (persons x coordinates) --> gt_future is for instance of size 39 x 2
        gt_future = batch_scene[self.obs_length]
        # Note: Since the first 9 frames of the scene correspond to observations
        # and since Python uses zero-based indexing, the first location prediction
        # sample (i.e. the 10th element in batch_scene) is accessed as
        # "batch_scene[9]" (i.e. "batch_scene[self.obs_length]")

        # #####################################################
        #           TODO: fill the following code
        # #####################################################

        # -----------------------------------------------------
        #                  Positive Samples
        # -----------------------------------------------------
        # cf. equ. 7 in paper "Social NCE: Contrastive Learning of Socially-aware
        # Motion Representations" (https://arxiv.org/abs/2012.11717):

        # positive sample ≡ ground truth + N(0, c_e * I )
        # positive sample: (persons of interest x coordinates)

        c_e = self.noise_local
        # Retrieving the location of the pedestrians of interest only
        personOfInterestLocation = gt_future[batch_split[0:-1], :]  # (persons of interest x coordinates) --> for instance: 8 x 2
        noise_pos = np.random.multivariate_normal([0, 0], np.array([[c_e, 0], [0, c_e]]), (personOfInterestLocation.shape[0]))  # (2,)
        #                      8 x 2                   1 x 2
        # sample_pos = personOfInterestLocation + noise.reshape(1, 2)
        #                      8 x 2             (2,)
        sample_pos = personOfInterestLocation + noise_pos

        # Retrieving the location of all pedestrians
        # sample_pos = gt_future[:, :, :] + np.random.multivariate_normal([0,0], np.array([[c_e, 0], [0, c_e]]))

        # -----------------------------------------------------
        #                  Negative Samples
        # -----------------------------------------------------
        # cf. fig 4b and eq. 6 in paper "Social NCE: Contrastive Learning of
        # Socially-aware Motion Representations" (https://arxiv.org/abs/2012.11717):

        nDirection = self.agent_zone.shape[0]
        nMaxNeighbour = 80 # TODO re-tune

        # sample_neg: (#persons of interest, #neigboor for this person of interest * #directions, #coordinates)
        # --> for instance: 8 x 12*9 x 2 = 8 x 108 x 2
        sample_neg = np.empty(
            (batch_split.shape[0] - 1, nDirection * nMaxNeighbour, 2))
        sample_neg[:] = np.NaN  # populating sample_neg with NaN values
        for i in range(batch_split.shape[0] - 1):
            # traj_primary = gt_future[batch_split[i]]
            traj_neighbour = gt_future[batch_split[i] + 1:batch_split[i + 1]]  # (number of neigbours x coordinates) --> for instance: 3 x 2

            noise_neg = np.random.multivariate_normal([0, 0], np.array([[c_e**2, 0], [0, c_e**2]]), (traj_neighbour.shape[0], self.agent_zone.shape[0])) # (2,)
            # negSampleNonSqueezed: (number of neighbours x directions x coordinates) --> for instance: 3 x 9 x 2
            #                            3 x 1 x 2                     1 x 9 x 2                (2,)
            negSampleNonSqueezed = traj_neighbour[:, None, :] + self.agent_zone[None, :, :] + noise_neg

            # negSampleSqueezed: (number of neighbours * directions x coordinates) --> for instance: 27 x 2
            negSampleSqueezed = negSampleNonSqueezed.reshape((-1, negSampleNonSqueezed.shape[2]))


            # Getting rid of too close negative samples
            vectForDist = negSampleSqueezed - personOfInterestLocation[i, :].reshape(-1, 2)
            dist = np.sqrt(vectForDist[:, 0]**2 + vectForDist[:, 1]**2)
            log_array = np.less_equal(dist, self.min_seperation)
            negSampleSqueezed[log_array] = np.nan

            # Getting rid of too far away negative samples
            # dist = np.linalg.norm(negSampleSqueezed - personOfInterestLocation[i, :].reshape(-1, 2))
            # log_array = np.greater_equal(dist, self.min_seperation)
            # negSampleSqueezed[log_array] = np.nan

            # Filling only the first part in the second dimension of sample_neg (leaving the rest as NaN values)
            sample_neg[i, 0:negSampleSqueezed.shape[0], :] = negSampleSqueezed

        # negative sample for everyone
        # the position          # the direction to look around     #some noise
        # sample_neg = gt_future[:,:,None,:] + self.agent_zone[None, None, :, :] + np.random.multivariate_normal([0,0], np.array([[c_e, 0], [0, c_e]]))

        # -----------------------------------------------------
        #       Remove negatives that are too close to person of interrest
        # -----------------------------------------------------




        # -----------------------------------------------------
        #       Remove negatives that are too easy (optional)
        # -----------------------------------------------------

        sample_pos = sample_pos.float()
        sample_neg = torch.tensor(sample_neg).float()
        return sample_pos, sample_neg




    def contrastive_loss(self, rel_output_list, targets, batch_split, batch_scene, batch_feat):

        HIDDEN_DIM= 128
        CONTRAST_DIM = 8
        head_projection = ProjHead(feat_dim=HIDDEN_DIM, hidden_dim=CONTRAST_DIM*4, head_dim=CONTRAST_DIM) # 2-layer MLP
        encoder_sample = SpatialEncoder(hidden_dim=CONTRAST_DIM, head_dim=CONTRAST_DIM) # another 2-layer MLP
        self.head_projection = head_projection
        self.encoder_sample = encoder_sample
        self.temperature = 0.07




        (sample_pos, sample_neg) = self._sampling_spatial(batch_scene, batch_split)
        visualize = 0


        if visualize:
            print("VISUALIZING")
            with open('outputs_saved.pkl', 'rb') as f:
                outputs_saved = pickle.load(f)

            for i in range(batch_split.shape[0] - 1):  # for each scene

                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt

                fig = plt.figure(frameon=False)
                fig.set_size_inches(16, 9)
                ax = fig.add_subplot(1, 1, 1)

                # Displaying the position of the neighbours
                # True position
                ax.scatter(batch_scene[self.obs_length, batch_split[i] + 1:batch_split[i + 1], 0].view(-1),
                           batch_scene[self.obs_length, batch_split[i] + 1:batch_split[i + 1], 1].view(-1),
                           label="neighbours true pos", c='g')

                # Negative sample
                ax.scatter(sample_neg[i, :, 0].view(-1),
                           sample_neg[i, :, 1].view(-1),
                           label="negative sample", c='r')

                # Trajectory planned (primary pedestrian)
                # Past trajectory
                ax.plot(outputs_saved[:8, batch_split[i], 0].detach(),
                           outputs_saved[:8, batch_split[i], 1].detach(), linestyle='-', marker='.',
                           label="past traj. main", c='pink')
                # Future trajectory
                ax.plot(outputs_saved[7:, batch_split[i], 0].detach(),
                        outputs_saved[7:, batch_split[i], 1].detach(), linestyle='-', marker='.',
                        label="future traj. main", c='m')

                # Trajectory planned (neighbours)
                for ind, j in enumerate(range(batch_split[i]+1, batch_split[i+1])):

                    if ind == 0:
                        # Past trajectory
                        ax.plot(outputs_saved[:8, j, 0].detach(),
                                outputs_saved[:8, j, 1].detach(), linestyle='-', marker='.',
                                label="past traj. neigh.", c='c')
                        # Future trajectory
                        ax.plot(outputs_saved[7:, j, 0].detach(),
                                outputs_saved[7:, j, 1].detach(), linestyle='-', marker='.',
                                label="future traj. neigh.", c='darkblue')
                    else:
                        # Past trajectory
                        ax.plot(outputs_saved[:8, j, 0].detach(),
                                outputs_saved[:8, j, 1].detach(), linestyle='-', marker='.', c='c')
                        # Future trajectory
                        ax.plot(outputs_saved[7:, j, 0].detach(),
                                outputs_saved[7:, j, 1].detach(), linestyle='-', marker='.', c='darkblue')

                # True position of primary pedestrian
                ax.scatter(batch_scene[self.obs_length, batch_split[i], 0],
                           batch_scene[self.obs_length, batch_split[i], 1],
                           label="person of interest true pos", c='b', zorder=10)
                # Positive sample
                ax.scatter(sample_pos[i, 0], sample_pos[i, 1],
                           label="positive sample", c='orange')

                ax.legend()
                ax.set_aspect('equal')
                ax.set_xlim(-7, 7)
                ax.set_ylim(-7, 7)
                plt.grid()
                fname = 'sampling_scene_{:d}.png'.format(i)
                plt.savefig(fname, bbox_inches='tight', pad_inches=0)
                plt.close(fig)
                print(f'displayed samples {i}')
            5/0



        # -----------------------------------------------------
        #              Lower-dimensional Embedding
        # -----------------------------------------------------
        # 12x40x8                             12x40x128
        interestsID = batch_split[0:-1]
        emb_obsv = self.head_projection(batch_feat[self.obs_length, interestsID, :])  # TODO should not the whole batch
        query = nn.functional.normalize(emb_obsv, dim=-1)  # TODO might not be dim 1

        # Embedding is not necessarily a dimension reduction process! Here we
        # want to find a way to compute the similarity btw. the motion features
        # (for this we have to increase the number of features!)
        # sample_neg: 8x108x2
        mask_normal_space = torch.isnan(sample_neg)
        sample_neg[torch.isnan(sample_neg)] = 0
        # key_neg : 8x108x8
        emb_pos = self.encoder_sample(sample_pos) # TODO cast to pytorch first
        emb_neg = self.encoder_sample(sample_neg)
        key_pos = nn.functional.normalize(emb_pos, dim=-1)
        key_neg = nn.functional.normalize(emb_neg, dim=-1)

        # -----------------------------------------------------
        #                   Compute Similarity
        # -----------------------------------------------------
        # similarity
        # 12x40x8   12x8x1x8
        sim_pos = (query[:, None, :] * key_pos[:, None, :]).sum(dim=-1)
        sim_neg = (query[:, None, :] * key_neg).sum(dim=-1)

        # 8x108
        mask_new_space = torch.logical_and(mask_normal_space[:, :, 0],
                                           mask_normal_space[:, :, 1])
        sim_neg[mask_new_space] = -10

        logits = torch.cat([sim_pos, sim_neg], dim=-1) / self.temperature  # Warning! Pos and neg samples are concatenated!

        # -----------------------------------------------------
        #                       NCE Loss
        # -----------------------------------------------------
        labels = torch.zeros(logits.size(0), dtype=torch.long)

        loss = self.criterion_contrast(logits, labels)
        #print(f"the contrast loss is {loss}")
        return loss



    def loss_criterion(self, rel_output_list, targets, batch_split, scores_fake, scores_real, step_type, batch_scene = None, batch_feat= None):
        """ Loss calculation function

        Parameters
        ----------
        rel_output_list : List of length k
            Each element of the list is Tensor [pred_length, num_tracks, 5]
            Predicted velocities of pedestrians as multivariate normal
            i.e. positions relative to previous positions
        targets : Tensor [pred_length, batch_size, 2]
            Groundtruth sequence of primary pedestrians of each scene
            ****VS****
            batch_scene: coordinates of agents in the scene, tensor of shape [obs_length + pred_length, total num of agents in the batch, 2]
        batch_split : Tensor [batch_size + 1]
            Tensor defining the split of the batch.
            Required to identify the primary tracks of each scene
        scores_real : Tensor [batch_size, ]
            Discriminator scores of groundtruth primary tracks
        scores_fake : Tensor [batch_size, ]
            Discriminator scores of prediction primary tracks
        step_type : 'g' / 'd'
            Determines whether to train the generator / discriminator

        Returns
        -------
        loss : Tensor [1,]
            The corresponding generator / discriminator loss
        """
        lossContrast = 0
        if step_type == 'd': # in case we are using the discriminator
            loss = gan_d_loss(scores_real, scores_fake)

        else: # in case we are NOT using the discriminator
            ## top-k loss
            loss = self.variety_loss(rel_output_list, targets, batch_split) # TODO delete line after, and add a constartive step here
            global contrast_weight
            if contrast_weight > 0:
                lossContrast = self.contrastive_loss(rel_output_list, targets, batch_split, batch_scene, batch_feat) # our contrastive learning loss
                loss += contrast_weight * lossContrast



            ## If discriminator used.
            if self.model.d_steps:
                loss += gan_g_loss(scores_fake)

        return loss, lossContrast

    def variety_loss(self, inputs, target, batch_split):
        """ Variety loss calculation as proposed in SGAN

        Parameters
        ----------
        inputs : List of length k
            Each element of the list is Tensor [pred_length, num_tracks, 5]
            Predicted velocities of pedestrians as multivariate normal
            i.e. positions relative to previous positions
        target : Tensor [pred_length, num_tracks, 2]
            Groundtruth sequence of primary pedestrians of each scene
        batch_split : Tensor [batch_size + 1]
            Tensor defining the split of the batch.
            Required to identify the primary tracks of each scene

        Returns
        -------
        loss : Tensor [1,]
            variety loss
        """

        iterative_loss = [] 
        for sample in inputs:
            sample_loss = self.criterion(sample[-self.pred_length:], target, batch_split)
            iterative_loss.append(sample_loss)

        loss = torch.stack(iterative_loss)
        loss = torch.min(loss, dim=0)[0]
        loss = torch.sum(loss)
        return loss

contrast_weight = 0

def main(epochs=25):
    parser = argparse.ArgumentParser()
    parser.add_argument('--epochs', default=epochs, type=int,
                        help='number of epochs')
    parser.add_argument('--save_every', default=5, type=int,
                        help='frequency of saving model (in terms of epochs)')
    parser.add_argument('--obs_length', default=9, type=int,
                        help='observation length')
    parser.add_argument('--pred_length', default=12, type=int,
                        help='prediction length')
    parser.add_argument('--start_length', default=0, type=int,
                        help='starting time step of encoding observation')
    parser.add_argument('--batch_size', default=8, type=int)
    parser.add_argument('-o', '--output', default=None,
                        help='output file')
    parser.add_argument('--disable-cuda', action='store_true',
                        help='disable CUDA')
    parser.add_argument('--path', default='trajdata',
                        help='glob expression for data files')
    parser.add_argument('--goals', action='store_true',
                        help='flag to consider goals of pedestrians')
    parser.add_argument('--loss', default='pred', choices=('L2', 'pred'),
                        help='loss objective, L2 loss (L2) and Gaussian loss (pred)')
    parser.add_argument('--type', default='vanilla',
                        choices=('vanilla', 'occupancy', 'directional', 'social', 'hiddenstatemlp', 's_att_fast',
                                 'directionalmlp', 'nn', 'attentionmlp', 'nn_lstm', 'traj_pool', 'nmmp', 'dir_social'),
                        help='type of interaction encoder')
    parser.add_argument('--sample', default=1.0, type=float,
                        help='sample ratio when loading train/val scenes')
    parser.add_argument('--contrast_weight', default=0.0, type=float,
                        help='weight of the contrast weight')
    ## Augmentations
    parser.add_argument('--augment', action='store_true',
                        help='perform rotation augmentation')
    parser.add_argument('--normalize_scene', action='store_true',
                        help='rotate scene so primary pedestrian moves northwards at end of observation')

    ## Loading pre-trained models
    pretrain = parser.add_argument_group('pretraining')
    pretrain.add_argument('--load-state', default=None,
                          help='load a pickled model state dictionary before training')
    pretrain.add_argument('--load-full-state', default=None,
                          help='load a pickled full state dictionary before training')
    pretrain.add_argument('--nonstrict-load-state', default=None,
                          help='load a pickled state dictionary before training')

    ## Sequence Encoder Hyperparameters
    hyperparameters = parser.add_argument_group('hyperparameters')
    hyperparameters.add_argument('--hidden-dim', type=int, default=128,
                                 help='LSTM hidden dimension')
    hyperparameters.add_argument('--coordinate-embedding-dim', type=int, default=64,
                                 help='coordinate embedding dimension')
    hyperparameters.add_argument('--pool_dim', type=int, default=256,
                                 help='output dimension of interaction vector')
    hyperparameters.add_argument('--goal_dim', type=int, default=64,
                                 help='goal embedding dimension')

    ## Grid-based pooling
    hyperparameters.add_argument('--cell_side', type=float, default=0.6,
                                 help='cell size of real world (in m) for grid-based pooling')
    hyperparameters.add_argument('--n', type=int, default=12,
                                 help='number of cells per side for grid-based pooling')
    hyperparameters.add_argument('--layer_dims', type=int, nargs='*', default=[512],
                                 help='interaction module layer dims for gridbased pooling')
    hyperparameters.add_argument('--embedding_arch', default='one_layer',
                                 help='interaction encoding arch for gridbased pooling')
    hyperparameters.add_argument('--pool_constant', default=0, type=int,
                                 help='background value (when cell empty) of gridbased pooling')
    hyperparameters.add_argument('--norm_pool', action='store_true',
                                 help='normalize the scene along direction of movement during grid-based pooling')
    hyperparameters.add_argument('--front', action='store_true',
                                 help='flag to only consider pedestrian in front during grid-based pooling')
    hyperparameters.add_argument('--latent_dim', type=int, default=16,
                                 help='latent dimension of encoding hidden dimension during social pooling')
    hyperparameters.add_argument('--norm', default=0, type=int,
                                 help='normalization scheme for input batch during grid-based pooling')

    ## Non-Grid-based pooling
    hyperparameters.add_argument('--no_vel', action='store_true',
                                 help='flag to not consider relative velocity of neighbours')
    hyperparameters.add_argument('--spatial_dim', type=int, default=32,
                                 help='embedding dimension for relative position')
    hyperparameters.add_argument('--vel_dim', type=int, default=32,
                                 help='embedding dimension for relative velocity')
    hyperparameters.add_argument('--neigh', default=4, type=int,
                                 help='number of nearest neighbours to consider')
    hyperparameters.add_argument('--mp_iters', default=5, type=int,
                                 help='message passing iterations in NMMP')

    ## SGAN-Specific Parameters
    hyperparameters.add_argument('--g_steps', default=1, type=int,
                                 help='number of steps of generator training')
    hyperparameters.add_argument('--d_steps', default=1, type=int,
                                 help='number of steps of discriminator training')
    hyperparameters.add_argument('--g_lr', default=1e-3, type=float,
                                 help='initial generator learning rate')
    hyperparameters.add_argument('--d_lr', default=1e-3, type=float,
                                 help='initial discriminator learning rate')
    hyperparameters.add_argument('--g_step_size', default=10, type=int,
                                 help='step_size of generator scheduler')
    hyperparameters.add_argument('--d_step_size', default=10, type=int,
                                 help='step_size of discriminator scheduler')
    hyperparameters.add_argument('--no_noise', action='store_true',
                                 help='flag to not add noise (i.e. deterministic model)')
    hyperparameters.add_argument('--noise_dim', type=int, default=16,
                                 help='dimension of noise z')
    hyperparameters.add_argument('--noise_type', default='gaussian',
                                 choices=('gaussian', 'uniform'),
                                 help='type of noise to be added')
    hyperparameters.add_argument('--k', type=int, default=1,
                                 help='number of samples for variety loss')

    args = parser.parse_args()
    global contrast_weight
    contrast_weight = args.contrast_weight # TODO refactor this cleaner
    ## Fixed set of scenes if sampling
    if args.sample < 1.0:
        torch.manual_seed("080819")
        random.seed(1)

    if not os.path.exists('OUTPUT_BLOCK/{}'.format(args.path)):
        os.makedirs('OUTPUT_BLOCK/{}'.format(args.path))
    if args.goals:
        args.output = 'OUTPUT_BLOCK/{}/sgan_goals_{}_{}.pkl'.format(args.path, args.type, args.output)
    else:
        args.output = 'OUTPUT_BLOCK/{}/sgan_{}_{}.pkl'.format(args.path, args.type, args.output)

    # configure logging
    from pythonjsonlogger import jsonlogger
    if args.load_full_state:
        file_handler = logging.FileHandler(args.output + '.log', mode='a')
    else:
        file_handler = logging.FileHandler(args.output + '.log', mode='w')
    file_handler.setFormatter(jsonlogger.JsonFormatter('%(message)s %(levelname)s %(name)s %(asctime)s'))
    stdout_handler = logging.StreamHandler(sys.stdout)
    logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler])
    logging.info({
        'type': 'process',
        'argv': sys.argv,
        'args': vars(args),
        'version': VERSION,
        'hostname': socket.gethostname(),
    })

    # refactor args for --load-state
    args.load_state_strict = True
    if args.nonstrict_load_state:
        args.load_state = args.nonstrict_load_state
        args.load_state_strict = False
    if args.load_full_state:
        args.load_state = args.load_full_state

    # add args.device
    args.device = torch.device('cpu')
    # if not args.disable_cuda and torch.cuda.is_available():
    #     args.device = torch.device('cuda')

    args.path = 'DATA_BLOCK/' + args.path
    ## Prepare data
    train_scenes, train_goals, _ = prepare_data(args.path, subset='/train/', sample=args.sample, goals=args.goals)
    val_scenes, val_goals, val_flag = prepare_data(args.path, subset='/val/', sample=args.sample, goals=args.goals)

    ## pretrained pool model (if any)
    pretrained_pool = None

    # create interaction/pooling modules
    pool = None
    if args.type == 'hiddenstatemlp':
        pool = HiddenStateMLPPooling(hidden_dim=args.hidden_dim, out_dim=args.pool_dim,
                                     mlp_dim_vel=args.vel_dim)
    elif args.type == 'nmmp':
        pool = NMMP(hidden_dim=args.hidden_dim, out_dim=args.pool_dim, k=args.mp_iters)
    elif args.type == 'attentionmlp':
        pool = AttentionMLPPooling(hidden_dim=args.hidden_dim, out_dim=args.pool_dim,
                                   mlp_dim_spatial=args.spatial_dim, mlp_dim_vel=args.vel_dim)
    elif args.type == 'directionalmlp':
        pool = DirectionalMLPPooling(out_dim=args.pool_dim)
    elif args.type == 'nn':
        pool = NN_Pooling(n=args.neigh, out_dim=args.pool_dim, no_vel=args.no_vel)
    elif args.type == 'nn_lstm':
        pool = NN_LSTM(n=args.neigh, hidden_dim=args.hidden_dim, out_dim=args.pool_dim)
    elif args.type == 'traj_pool':
        pool = TrajectronPooling(hidden_dim=args.hidden_dim, out_dim=args.pool_dim)
    elif args.type == 's_att_fast':
        pool = SAttention_fast(hidden_dim=args.hidden_dim, out_dim=args.pool_dim)
    elif args.type != 'vanilla':
        pool = GridBasedPooling(type_=args.type, hidden_dim=args.hidden_dim,
                                cell_side=args.cell_side, n=args.n, front=args.front,
                                out_dim=args.pool_dim, embedding_arch=args.embedding_arch,
                                constant=args.pool_constant, pretrained_pool_encoder=pretrained_pool,
                                norm=args.norm, layer_dims=args.layer_dims, latent_dim=args.latent_dim)

    # generator
    lstm_generator = LSTMGenerator(embedding_dim=args.coordinate_embedding_dim, hidden_dim=args.hidden_dim,
                                   pool=pool, goal_flag=args.goals, goal_dim=args.goal_dim, noise_dim=args.noise_dim,
                                   no_noise=args.no_noise, noise_type=args.noise_type)

    # discriminator
    lstm_discriminator = LSTMDiscriminator(embedding_dim=args.coordinate_embedding_dim,
                                           hidden_dim=args.hidden_dim, pool=copy.deepcopy(pool),
                                           goal_flag=args.goals, goal_dim=args.goal_dim)

    # GAN model
    model = SGAN(generator=lstm_generator, discriminator=lstm_discriminator, g_steps=args.g_steps,
                 d_steps=args.d_steps, k=args.k)

    # Optimizer and Scheduler
    g_optimizer = torch.optim.Adam(model.generator.parameters(), lr=args.g_lr, weight_decay=1e-4)
    d_optimizer = torch.optim.Adam(model.discriminator.parameters(), lr=args.d_lr, weight_decay=1e-4)
    g_lr_scheduler = torch.optim.lr_scheduler.StepLR(g_optimizer, args.g_step_size)
    d_lr_scheduler = torch.optim.lr_scheduler.StepLR(d_optimizer, args.d_step_size)
    start_epoch = 0

    # Loss Criterion
    if args.loss == 'L2':
        criterion = L2Loss(keep_batch_dim=True)
    else:
        criterion = PredictionLoss(keep_batch_dim=True)

    # train
    if args.load_state:
        # load pretrained model.
        # useful for tranfer learning
        print("Loading Model Dict")
        with open(args.load_state, 'rb') as f:
            checkpoint = torch.load(f)
        pretrained_state_dict = checkpoint['state_dict']
        model.load_state_dict(pretrained_state_dict, strict=args.load_state_strict)

        if args.load_full_state:
        # load optimizers from last training
        # useful to continue model training
            print("Loading Optimizer Dict")
            g_optimizer.load_state_dict(checkpoint['g_optimizer'])
            d_optimizer.load_state_dict(checkpoint['d_optimizer'])
            g_lr_scheduler.load_state_dict(checkpoint['g_lr_scheduler'])
            d_lr_scheduler.load_state_dict(checkpoint['d_lr_scheduler'])
            start_epoch = checkpoint['epoch']


    #trainer
    trainer = Trainer(model, g_optimizer=g_optimizer, g_lr_scheduler=g_lr_scheduler, d_optimizer=d_optimizer,
                      d_lr_scheduler=d_lr_scheduler, device=args.device, criterion=criterion,
                      batch_size=args.batch_size, obs_length=args.obs_length, pred_length=args.pred_length,
                      augment=args.augment, normalize_scene=args.normalize_scene, save_every=args.save_every,
                      start_length=args.start_length, val_flag=val_flag)
    trainer.loop(train_scenes, val_scenes, train_goals, val_goals, args.output, epochs=args.epochs, start_epoch=start_epoch)


if __name__ == '__main__':
    main()
