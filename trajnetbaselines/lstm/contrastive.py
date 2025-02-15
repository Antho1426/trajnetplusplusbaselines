import math
import numpy as np
import torch
import torch.nn as nn


class SocialNCE():
    """
        Social NCE: Contrastive Learning of Socially-aware Motion Representations (https://arxiv.org/abs/2012.11717)
    """

    def __init__(self, obs_length, pred_length, head_projection, encoder_sample,
                 temperature, horizon, sampling):

        # problem setting
        self.obs_length = obs_length
        self.pred_length = pred_length

        # nce models
        self.head_projection = head_projection
        self.encoder_sample = encoder_sample

        # nce loss
        self.criterion = nn.CrossEntropyLoss()

        # nce param
        self.temperature = temperature
        self.horizon = horizon

        # sampling param
        self.noise_local = 0.05  # TODO maybe 0.1    0.025
        self.min_seperation = 0.2  # TODO increase this ? (uncomfortable zone is up to 20[cm])
        self.max_seperation = 5  # TODO increase this ? (anyway not used for the moment)
        self.agent_zone = self.min_seperation * torch.tensor(
            [[1.0, 0.0], [-1.0, 0.0], [0.0, 1.0], [0.0, -1.0], [0.707, 0.707],
             [0.707, -0.707], [-0.707, 0.707], [-0.707, -0.707], [0.0, 0.0]])

        self.sampling = sampling  # by maxime

    def spatial(self, batch_scene, batch_split, batch_feat):
        """
            Social NCE with spatial samples, i.e., samples are locations at a specific time of the future
            Input:                                                                  ( 9       +    12   )
                batch_scene: coordinates of agents in the scene, tensor of shape [obs_length + pred_length, total num of agents in the batch, 2]
                batch_split: index of scene split in the batch, tensor of shape [batch_size + 1]
                batch_feat: encoded features of observations, tensor of shape [pred_length, scene, feat_dim]
                                                                                            ^ #person maybe ?
            Output:
                loss: social nce loss
        """

        # -----------------------------------------------------
        #               Visualize Trajectories 
        #       (Use this block to visualize the raw data)
        # -----------------------------------------------------

        # for i in range(batch_split.shape[0] - 1):
        #     traj_primary = batch_scene[:, batch_split[i]] # [time, 2]
        #     traj_neighbour = batch_scene[:, batch_split[i]+1:batch_split[i+1]] # [time, num, 2]
        #     plot_scene(traj_primary, traj_neighbour, fname='scene_{:d}.png'.format(i))
        # import pdb; pdb.set_trace() # --> to do an embedded breakpoint with Python (without PyCharm debugger)

        # #####################################################
        #           TODO: fill the following code
        # #####################################################

        # hint from navigation repo : https://github.com/vita-epfl/social-nce-crowdnav/blob/main/crowd_nav/snce/contrastive.py
        # hint from forecasting repo: https://github.com/YuejiangLIU/social-nce-trajectron-plus-plus/blob/master/trajectron/snce/contrastive.py

        # -----------------------------------------------------
        #               Contrastive Sampling 
        # -----------------------------------------------------

        # batch_split : (8) (ID of the start of the scene and of the person of interest)
        # batch_scene : (time x persons (i.e. personsOfInterest and neighbours) x coordinate)
        #                (21)  x 40 x 2
        # traj_primary: 21x2 (time x coordinate)
        # traj_neighbour: 21x3x2 (time x persons x coordinate)

        (sample_pos, sample_neg) = self._sampling_spatial(batch_scene, batch_split)

        # Scenes visualisation
        # Displaying the position of the primary pedestrian, the neighbours,
        # the positive sample and the negative samples
        visualize = 0
        if visualize:

            for i in range(batch_split.shape[0] - 1): # looping over the scenes

                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt

                fig = plt.figure(frameon=False)
                fig.set_size_inches(16, 9)
                ax = fig.add_subplot(1, 1, 1)

                # Person of interest true position
                ax.scatter(batch_scene[self.obs_length, batch_split[i], 0],
                           batch_scene[self.obs_length, batch_split[i], 1],
                           label="person of interest true pos")

                # Positive sample
                ax.scatter(sample_pos[i, 0], sample_pos[i, 1],
                           label="positive sample")

                # Neighbours true position
                ax.scatter(batch_scene[self.obs_length, batch_split[i] + 1:batch_split[i + 1], 0].view(-1),
                           batch_scene[self.obs_length, batch_split[i] + 1:batch_split[i + 1], 1].view(-1),
                           label="neighbours true pos")

                # Negative samples
                ax.scatter(sample_neg[i, :, 0].view(-1),
                           sample_neg[i, :, 1].view(-1),
                           label="negative sample")

                ax.legend()
                ax.set_aspect('equal')
                ax.set_xlim(-7, 7)
                ax.set_ylim(-7, 7)
                plt.grid()
                fname = 'sampling_scene_{:d}.png'.format(i)
                plt.savefig(fname, bbox_inches='tight', pad_inches=0)
                plt.close(fig)
                print(f'displayed samples {i}')

            # Unavoidable breakpoint
            5/0

        # -----------------------------------------------------
        #              Lower-dimensional Embedding
        # -----------------------------------------------------
        # 8
        interestsID = batch_split[0:-1]
        # 8 x 8
        emb_obsv = self.head_projection(batch_feat[self.obs_length, interestsID, :]) # passing the social representation (of the observed trajectories) into the "projection head" to get an embedded observation
        # 8 x 8
        query = nn.functional.normalize(emb_obsv, dim=-1) # normalizing the embedded observation to get the query

        # Embedding is not necessarily a dimension reduction process! Here we
        # want to find a way to compute the similarity btw. the motion features
        # (for this we have to increase the number of features (so that's not
        # dimension reduction here)!)
        #                          sample_neg: 8 x 108 x 2
        mask_normal_space = torch.isnan(sample_neg)
        # Replacing the NaN values in "sample_neg" by "0" to avoid having NaN values in the "sample encoder"
        sample_neg[torch.isnan(sample_neg)] = 0

        # emb_pos: 8 x 8                sample_pos: 8 x 2
        emb_pos = self.encoder_sample(sample_pos) # passing the positive samples into the "sample encoder" to get an embedding of the positive samples
        # 8 x 8
        key_pos = nn.functional.normalize(emb_pos, dim=-1) # normalizing the embedded positive samples to get the "positive key"

        # 8 x 108 x 8
        emb_neg = self.encoder_sample(sample_neg) # passing the negative samples into the "sample encoder" to get an embedding of the negative samples
        # 8 x 108 x 8
        key_neg = nn.functional.normalize(emb_neg, dim=-1) # normalizing the embedded negative samples to get the "negative key"

        # -----------------------------------------------------
        #                   Compute Similarity 
        # -----------------------------------------------------
        # Computing the similarity is equivalent to compute the dot product between two tensors
        # In the paper, they use the cosine similarity (which is the same similarity we used
        # except the fact that it is additionally normalized)

        # sim_pos: 8 x 1   query: 8 x 8      key_pos: 8 x 8
        sim_pos = (query[:, None, :] * key_pos[:, None, :]).sum(dim=-1) # computing the similarity between the query and the positive key
        # sim_neg: 8 x 108   query: 8 x 8      key_neg: 8 x 108 x 8
        sim_neg = (query[:, None, :] * key_neg).sum(dim=-1) # computing the similarity between the query and the negative key

        # mask_new_space: 8 x 108          mask_normal_space: 8 x 108 x 2
        mask_new_space = torch.logical_and(mask_normal_space[:, :, 0],
                                           mask_normal_space[:, :, 1])

        # 8 x 108
        sim_neg[mask_new_space] = -10 # setting the previously NaN values of the "negative samples" to "-10" to make sure those will have a very little influence on the logits used to compute the loss

        logits = torch.cat([sim_pos, sim_neg], dim=-1) / self.temperature # concatenating positive and negative samples
        # (As in the paper, we divide here the similarity between the query and the key by the temperature;
        # temperature scaling can affect the feature vectors by increasing the similarity (in case temperature < 1);
        # we used the default value of temperature of 0.07)

        # -----------------------------------------------------
        #                       NCE Loss
        # -----------------------------------------------------
        # labels: 8 (= number of primary pedestrians)
        labels = torch.zeros(logits.size(0), dtype=torch.long)
        loss = self.criterion(logits, labels) # computing the "CrossEntropyLoss" loss with a labels being a tensor of zero values is a hack to implement the loss used in the paper (equation 1, p.3)
        #print(f"the contrast loss is {loss}")
        return loss


    def event(self, batch_scene, batch_split, batch_feat):
        """
            Social NCE with event samples, i.e., samples are spatial-temporal events at various time steps of the future
        """
        (sample_pos, sample_neg) = self._sampling_event(batch_scene, batch_split)


        # -----------------------------------------------------
        #              Lower-dimensional Embedding
        # -----------------------------------------------------

        interestsID = batch_split[0:-1]
        emb_obsv = self.head_projection(batch_feat[self.obs_length, interestsID, :])
        query = nn.functional.normalize(emb_obsv, dim=-1)

        # Embedding is not necessarily a dimension reduction process! Here we
        # want to find a way to compute the similarity btw. the motion features
        # (for this we have to increase the number of features!)
        # sample_neg: 8x108x2
        mask_normal_space = torch.isnan(sample_neg)

        sample_neg[torch.isnan(sample_neg)] = 0
        # key_neg : 8x108x8
        emb_pos = self.encoder_sample(sample_pos)
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

        logits = torch.cat([sim_pos, sim_neg], dim=-1) / self.temperature # Warning! Pos and neg samples are concatenated!

        # -----------------------------------------------------
        #                       NCE Loss
        # -----------------------------------------------------
        labels = torch.zeros(logits.size(0), dtype=torch.long)
        loss = self.criterion(logits, labels)
        #print(f"the contrast loss is {loss}")
        return loss


    def _sampling_event(self, batch_scene, batch_split):

        gt_future = batch_scene[self.obs_length: self.obs_length+self.pred_length]

        #positive sample
        c_e = self.noise_local
        # Retrieving the location of the pedestrians of interest only
        personOfInterestLocation = gt_future[:, batch_split[0:-1], :] # (persons of interest x coordinates) --> for instance: 8 x 2
        noise_pos = np.random.multivariate_normal([0, 0], np.array([[c_e, 0], [0, c_e]]), (self.pred_length, 8))  # (2,)
        #                      8 x 2                   1 x 2
        # sample_pos = personOfInterestLocation + noise.reshape(1, 2)
        #                      8 x 2             (2,)
        sample_pos = personOfInterestLocation + noise_pos



        #_______negative sample____________
        nDirection = self.agent_zone.shape[0]
        nMaxNeighbour = 80

        # sample_neg: (#persons of interest, #neigboor for this person of interest * #directions, #coordinates)
        # --> for instance: 8 x 12*9 x 2 = 8 x 108 x 2
        sample_neg = np.empty((self.pred_length, batch_split.shape[0] - 1, nDirection * nMaxNeighbour, 2))
        sample_neg[:] = np.NaN  # populating sample_neg with NaN values
        for i in range(batch_split.shape[0] - 1):

            traj_neighbour = gt_future[:, batch_split[i] + 1:batch_split[i + 1]]  # (number of neigbours x coordinates) --> for instance: 3 x 2

            noise_neg = np.random.multivariate_normal([0, 0], np.array([[c_e, 0], [0, c_e]]), (self.pred_length, traj_neighbour.shape[1], self.agent_zone.shape[0])) # (2,)
            # negSampleNonSqueezed: (time x number of neighbours x directions x coordinates)
            #                            12x 3 x 1 x 2                     12x 3 x 9 x 2                (12,3,9,2)
            negSampleNonSqueezed = traj_neighbour[:,:, None, :] + self.agent_zone[None, None, :, :] + noise_neg

            # negSampleSqueezed: (time x number of neighbours * directions x coordinates)
            negSampleSqueezed = negSampleNonSqueezed.reshape((self.pred_length,-1, negSampleNonSqueezed.shape[-1]))

            # Filling only the first part in the second dimension of sample_neg (leaving the rest as NaN values)
            sample_neg[:, i, 0:negSampleSqueezed.shape[1], :] = negSampleSqueezed

        sample_pos = sample_pos.float()
        sample_neg = torch.tensor(sample_neg).float()
        return sample_pos, sample_neg


    def _sampling_spatial(self, batch_scene, batch_split):
        # "_" indicates that this is a private function that we can only access from the class
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
        nMaxNeighbour = 80 # with 50 it was not enough and had to increase it

        # sample_neg: (#persons of interest, #neigboor for this person of interest * #directions, #coordinates)
        # --> for instance: 8 x 12*9 x 2 = 8 x 108 x 2
        sample_neg = np.empty((batch_split.shape[0] - 1, nDirection * nMaxNeighbour, 2))
        sample_neg[:] = np.NaN  # populating the whole sample_neg with NaN values everywhere
        for i in range(batch_split.shape[0] - 1):
            # traj_primary = gt_future[batch_split[i]]
            traj_neighbour = gt_future[batch_split[i] + 1:batch_split[i + 1]]  # (number of neigbours x coordinates) --> for instance: 3 x 2

            noise_neg = np.random.multivariate_normal([0, 0], np.array([[c_e**2, 0], [0, c_e**2]]), (traj_neighbour.shape[0], self.agent_zone.shape[0])) # (2,)
            # negSampleNonSqueezed: (number of neighbours x directions x coordinates) --> for instance: 3 x 9 x 2
            #                            3 x 1 x 2                     1 x 9 x 2                (2,)
            negSampleNonSqueezed = traj_neighbour[:, None, :] + self.agent_zone[None, :, :] + noise_neg

            # negSampleSqueezed: (number of neighbours * directions x coordinates) --> for instance: 27 x 2
            negSampleSqueezed = negSampleNonSqueezed.reshape((-1, negSampleNonSqueezed.shape[2]))

            # -----------------------------------------------------
            #       Remove negatives that are too close to person of interrest
            # -----------------------------------------------------

            # Getting rid of too close negative samples to the primary pedestrian
            # (Those negative samples would be too close by default --> no need to analyze the output)
            dist = np.linalg.norm(negSampleSqueezed - personOfInterestLocation[i, :].reshape(-1, 2))
            log_array = np.less_equal(dist, self.min_seperation)
            negSampleSqueezed[log_array] = np.nan

            # Filling only the first part in the second dimension of sample_neg (leaving the rest as NaN values)
            sample_neg[i, 0:negSampleSqueezed.shape[0], :] = negSampleSqueezed

            # -----------------------------------------------------
            #       Remove negatives that are too easy (optional)
            # -----------------------------------------------------

        sample_pos = sample_pos.float()
        sample_neg = torch.tensor(sample_neg).float()
        return sample_pos, sample_neg


class EventEncoder(nn.Module):
    """
        Event encoder that maps an sampled event (location & time) to the embedding space
    """

    def __init__(self, hidden_dim, head_dim):
        super(EventEncoder, self).__init__()
        self.temporal = nn.Sequential(
            nn.Linear(1, hidden_dim),
            nn.ReLU(inplace=True)
        )
        self.spatial = nn.Sequential(
            nn.Linear(2, hidden_dim),
            nn.ReLU(inplace=True)
        )
        self.encoder = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim, head_dim)
        )

    def forward(self, state, time):
        emb_state = self.spatial(state)
        emb_time = self.temporal(time)
        out = self.encoder(torch.cat([emb_time, emb_state], axis=-1))
        return out


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


def plot_scene(primary, neighbor, fname):
    """
        Plot raw trajectories
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig = plt.figure(frameon=False)
    fig.set_size_inches(16, 9)
    ax = fig.add_subplot(1, 1, 1)

    ax.plot(primary[:, 0], primary[:, 1], 'k-')
    for i in range(neighbor.size(1)):
        ax.plot(neighbor[:, i, 0], neighbor[:, i, 1], 'b-.')

    ax.set_aspect('equal')
    plt.grid()
    plt.savefig(fname, bbox_inches='tight', pad_inches=0)
    plt.close(fig)