# TrajNet++ : The Trajectory Forecasting Framework

![Python](https://img.shields.io/badge/python-v3.6+-green.svg)
![Python](https://img.shields.io/badge/python-v3.8+-green.svg)
[![wakatime](https://wakatime.com/badge/github/Antho1426/trajnetplusplusbaselines.svg)](https://wakatime.com/badge/github/Antho1426/trajnetplusplusbaselines)
[![Open Source? Yes!](https://badgen.net/badge/Open%20Source%20%3F/Yes%21/blue?icon=github)](https://github.com/Naereen/badges/)


 **Course: CIVIL-459 Deep Learning for Autonomous Vehicles**

 > Teacher: Alexandre Alahi
 
 > Assistants: Parth Kothari, George Adaimi

 > Students: Maxime Gardoni, Anthony Guinchard, Robert Pieniuta

PyTorch implementation of the paper [*Human Trajectory Forecasting in Crowds: A Deep Learning Perspective*](https://arxiv.org/pdf/2007.03639.pdf).

<img src="docs/train/cover.png" style="width:800px;">

This project is conducted in the frame of the EPFL course *CIVIL-459 Deep Learning for Autonomous Vehicles*. It is forked from the original [TrajNet++ repository](https://github.com/vita-epfl/trajnetplusplusbaselines) elaborated by VITA lab from EPFL.

TrajNet++ is a large scale interaction-centric trajectory forecasting benchmark comprising explicit agent-agent scenarios. The framework provides proper indexing of trajectories by defining a hierarchy of trajectory categorization. In addition, it provides an extensive evaluation system to test the gathered methods for a fair comparison. In the evaluation, the framework goes beyond the standard distance-based metrics and introduces novel metrics that measure the capability of a model to emulate pedestrian behavior in crowds. Finally, TrajNet++ provides code implementations of > 10 popular human trajectory forecasting baselines.

## Table of contents

1. [ Milestone 1: Getting Started ](#mi_1)

 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;1.1 [ Visualizations ](#mi_1_vis) 
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;1.2 [ Qualitative evaluation ](#mi_1_qual_eval)

2. [ Milestone 2: Implementing Social Contrastive Learning ](#mi_2)

 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;2.1 [ Spatial sampling ](#mi_2_sp)
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;2.2 [ Contrastive learning implementation ](#mi_2_cli)
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;2.3 [ Training ](#mi_2_tr)
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;2.4 [ Results ](#mi_2_res)
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;2.5 [ Observations ](#mi_2_obs)

3. [ Milestone 3: Multimodal Prediction & TrajNet++ Challenge ](#mi_3)

 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;3.1 [ Implementation details ](#mi_3_1) 
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;3.2 [ Results ](#mi_3_2)
 
 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;3.3 [ Conclusion ](#mi_3_3) 

<a name="mi_1"></a>
## ᐅ Milestone 1: Getting Started

The purpose of this first milestone is to get used to work with the TrajNet++ framework and its command line interface for training and evaluating models.

<a name="mi_1_vis"></a>
### 1) Visualizations

Visualizations of 3 test scenes qualitatively comparing outputs of the *Vanilla* model and *D-Grid* model both trained during 2 epochs:

<img src="README_figures/milestone_1/predictions_vanilla_d-grid/visualize.scene46482.png" style="height:300px;">

<img src="README_figures/milestone_1/predictions_vanilla_d-grid/visualize.scene44949.png" style="height:300px;">

<img src="README_figures/milestone_1/predictions_vanilla_d-grid/visualize.scene47642.png" style="height:300px;">

Those 3 visualizations clearly demonstrate the superiority of the *D-Grid* model over the *Vanilla*  one in terms of both Average Displacement Error (ADE) and Final Displacement Error (FDE) already for only 2 epochs of training.

Command used to train the *Vanilla* model on 2 epochs:


```
python -m trajnetbaselines.lstm.trainer --epochs 2 --path five_parallel_synth_split --augment
```

Command used to train the *D-Grid* model on 2 epochs:

```
python -m trajnetbaselines.lstm.trainer --epoch 2 --path five_parallel_synth_split --type 'directional' --goals --augment
```

<a name="mi_1_qual_eval"></a>
### 2) Qualitative evaluation

Qualitative evaluation (Results.png):

<img src="README_figures/milestone_1/Results.png">


<a name="mi_2"></a>
## ᐅ Milestone 2: Implementing Social Contrastive Learning

Based on our familiarity with the Trajnet++ framework, the purpose of this second milestone is to apply social contrastive learning to the D-LSTM (i.e. *D-Grid*) model. More information about this method can be found in the paper [*Social NCE: Contrastive Learning of Socially-aware Motion Representations*](https://arxiv.org/pdf/2012.11717.pdf) written by researchers from VITA lab of EPFL. Briefly explained, contrastive learning combined with negative data augmentation has been a promising technique to boost the robustness of forecasting models. In addition, it has been shown that using social contrastive learning helps to reduce the percentage of collision (COL). In fact, this strategy allows to locally treat trajectories to avoid other pedestrians (in comparison with a model that has been trained without contrastive loss). In this second milestone we have hence implemented contrastive learning and sampling methods (both positive and negative) as suggested in the [reference paper](https://arxiv.org/pdf/2012.11717.pdf).

Given time constraints, it was chosen not to implement event sampling in order to better focus on the spatial sampling and its hyperparameters tuning.

<a name="mi_2_sp"></a>
### 1) Spatial sampling

Here is how our spatial sampling is performed:

* Positive sampling: the ground truth position of the primary pedestrian is selected, and a small noise is added in order to avoid overfitting.

* Negative sampling: the generation of negative samples was a bit more challenging, since the number of neighbours is not constant and can vary from scene to scene. Here are the three main points of our proposed solution:

	i. A constant maximum number of neighbours is defined, and a big tensor of negative samples is built based on this constant.

	ii. The first part of this tensor is filled with the present neighbour position, on which we add shifts in 9 different directions, and again a small noise to prevent overfitting.
	
	iii. The leftover part of this tensor is filled with NaN values (missing neighbours).

Example of scene with 4 neighbours presenting both positive and negative samples used to train our model following the safety-driven sampling strategy proposed in the [reference paper](https://arxiv.org/pdf/2012.11717.pdf):

<img src="README_figures/milestone_2/positive_and_negative_sampling_visualizations/sampling_scene_4.png" style="height:300px;">


<a name="mi_2_cli"></a>
### 2) Contrastive learning implementation

After sampling, the following steps were performed in our contrastive learning process:

1. Saving a mask for the present neighbours.
2. Conversion of NaN values to 0 to cancel their effect in the following MLP.
3. Lower-dimensional embedding of the past trajectories, negative samples and positive samples computation via an MLP encoder.
4. Normalization of those lower-dimensional embeddings.
5. Similarity computation between positive/negative embeddings and the predicted trajectory.
6. Using the mask, setting the similarity value for missing sample to -10 to prevent them from interfering with the loss computation.
7. Loss computation


<a name="mi_2_tr"></a>
### 3) Training

**Training procedure**

The models trained on both real (`real_data`) and synthetic (`synth_data`) data obtained from milestone 1 (25 epochs) were fine-tuned using this new NCE (Noise Contrastive Estimation) loss function designed for contrastive learning again on both real and synthetic data.

**Hyperparameters tuning**

The parameters to be tuned were the following:

* Contrastive weight (`contrastive_weight`): relative weight of the contrastive loss with respect to the original loss.
* Learning rate (`lr`) of the network: a too high learning rate might destroy the previously learned net; on the other hand, a too low learning rate might be ineffective to pursue learning.
* Number of additional epochs (`epochs`) used for fine-tuning.


<a name="mi_2_res"></a>
### 4) Results

Here is our results comparison in terms of FDE and Col-I of our D-LSTM models trained without (milestone 1) and with (milestone 2) contrastive loss.

**Milestone 1**

| Subm. | Epochs | lr       | FDE  | COL-I |
|-------|--------|----------|------|-------|
| 1     | 15     | 1.e-03   | 1.22 | 6.32  |
| 2     | 25     | 1.e-03   | 1.22 | 6.03  |


**Milestone 2**

| Subm. | Start epoch | Add. epochs | lr    | contrast_weight | FDE  | COL-I |
|-------|-------------|-------------|-------|-----------------|------|-------|
| 1     | 30          | 10          | 1e-04 | 2               | 1.22 | 6.03  |
| 2     | 30          | 10          | 1e-04 | 5               | 1.23 | 5.85  |
| 3     | 30          | 15          | 1e-02 | 1               | 1.34 | 7.16  |
| 4     | 30          | 10          | 1e-03 | 3               | 1.25 | **5.25**  |
| 5     | 30          | 15          | 1e-03 | 4               | 1.22 | 6.5   |
| 6     | 25          | 10          | 1e-03 | 5               | 1.23 | 5.61  |
| 7     | 30          | 10          | 5e-03 | 1               | 1.27 | 6.32  |
| 8     | 25          | 15          | 1e-03 | 2               | 1.23 | 5.85  |
| 9     | 25          | 15          | 5e-04 | 4               | **1.21** | 6.44  |
| 10    | 25          | 15          | 1e-03 | 2.5             | 1.22 | 5.55  |
| **11**    | **25**          | **15**          | **1e-03** | **0.5**             | **1.22** | **5.43**  |


[*Submission 11 link*](https://www.aicrowd.com/challenges/trajnet-a-trajectory-forecasting-challenge/submissions/139231).

Commands used to further train the model of milestone 1 and obtain similar results to the ones of submission 11:

*Training on real_data*
```
python -m trajnetbaselines.lstm.trainer --path real_data --type directional --augment --epochs 15 --contrast_weight 0.5 --lr 1e-3 --load-full-state OUTPUT_BLOCK/real_data/lstm_directional_None.pkl.epoch25.state
```

*Training on synth_data*
```
python -m trajnetbaselines.lstm.trainer --path synth_data --goals --type directional --augment --epochs 15 --contrast_weight 0.5 --lr 1e-3 --load-full-state OUTPUT_BLOCK/synth_data/lstm_goals_directional_None.pkl.epoch25.state
```



<a name="mi_2_obs"></a>
### 5) Observations

From the results we obtain, we can see that in comparison to our best submission for milestone 1 (submission 2) the contrastive loss managed to improve the COL-I measure (Prediction Collision) by at most 13% (submission 4 of milestone 2). This observation allows us to conclude that the contrastive learning and the negative data augmentation (i.e. creating negative samples around neighbours) implemented in this second milestone effectively help to reduce the amount of collisions and hence to predict more realistic pedestrian trajectories.

Concerning the FDE (Final Displacement Error), even if this metric didn't decrease that much in comparison with milestone 1 (our lowest FDE was obtained with submission 9), we can conclude that this new NCE loss remains all the same competitive in the FDE by not impacting it too much.

To sum up, the overall best performing model we trained (i.e. the one embedding jointly the lowest FDE and COL-I) is the one of submission 11. As said previously, the learning rate must be chosen wisely to allow the model to learn effectively. In our case, we have chosen to keep its default value (1e-3). In the first submissions we made, we started with a model that we had not submitted for milestone 1, but that we had trained to 30 epochs. Later, for fairer comparisons, we chose rather to fine-tune the best model we had submitted to AICrowd (i.e. the one from submission 2 of milestone 1 trained to 25 epochs) even though it has been trained on 5 less epochs. We also observed that there doesn't seem to be much improvement between 10 and 15 additional epochs. Indeed, the learning curve should apparently gently tend towards a zero slope from 10 additional epochs. Finally, the contrastive weight (introduced in milestone 2) was the new critical hyperparameter we had to tune. From our tests, we deduce that an optimal value for this parameter is situated between 0.5 and 3 in order to influence the global loss in a reasonable way and to reduce the COL-I metric.


<a name="mi_3"></a>
## ᐅ Milestone 3: Multimodal Prediction & TrajNet++ Challenge

In milestone 3, we trained the three off the shelf multimodal baselines of the TrajNet++ framework, i.e. Social GAN, variety loss (SGAN without the discriminator) and conditional variational autoencoder (VAE). We carefully took into account results obtained from milestone 2, especially the power of contrastive learning to obtain better scores. We have successfully incorporated new modifications in the existing trajectory forecasting models based on the knowledge learned in the course, including fine-tuning of the contrastive weight parameter. In the end, for the open-ended part, we decided to use SGAN without discriminator and contrastive learning to combine the multimodal power of GAN network with the collision rate reduction effect of NCE. With a minimal FDE, this combination of techniques allowed us to get a high position on AICrowd ranking. The figure below depicts the network architecture applied in the 3rd milestone.


**Network architecture**

<img src="README_figures/milestone_3/architecture_diagram.png">


<a name="mi_3_1"></a>
### 1) Implementation details

The implementation of contrastive learning in the Social GAN multimodal baseline was highly similar to the one applied in LSTM. In fact, we basically replicated the function `contrastive_loss` used in milestone 2 to compute the contrastive loss with the LSTM-based network inside the function `loss_criterion` of `sgan/trainer.py`. Nearly all the parameters needed in this task were already prepared to compute the variety loss and we actually just had to find a way to retrieve the parameters `batch_scene` and `batch_feat`. `batch_scene` (i.e. the tensor containing the trajectories of all the pedestrians in the scene) is available in the `train_batch` function from which the `loss_criterion` function is called. Hence, we modified the signature of `loss_criterion` to integrate this new parameter. As for the other variable `batch_feat` (i.e. the tensor containing the encoded representation of the observed trajectories created by the interaction and sequence encoder), we had just to modify the `forward` method of the `SGAN` class in `sgan.py` such that it returns the `batch_feat` computed by the generator for the last predicted scene. Of course, we had to call the projection head and the spatial encoder at the top of the `contrastive_loss` (along with other parameters such as the `temperature` required for contrastive learning), but apart from that the content and the principle of positive and negative sampling used in this `contrastive_loss` function are exactly the same as the ones used in milestone 2.



<a name="mi_3_2"></a>
### 2) Results

**Tables**

We trained Social GAN (with the interaction module of type `social`) and conditional variational autoencoder (VAE) (here with the type `directional`) during 15 epochs, both with a learning rate of 1e-3. Following table summarises the obtained FDE and COL-I as evaluated by AICrowd. 

| Baseline   | FDE  | COL-I |
|------------|------|-------|
| Social GAN | 1.33 | 8.11  |
| VAE        | 4.92 | 14.98 |

The next table summarizes the results in terms of FDE and COL-I we obtained from AICrowd using our Social GAN without discriminator and contrastive learning for three different values of the `contrast_weight` parameter. Here again, we trained our models from scratch during 15 epochs, with a learning rate of 1e-3.

| contrast_weight | FDE  | COL-I |
|-----------------|------|-------|
| 2               | 1.19 | 6.26  |
| 1               | **1.17** | 7.22  |
| 0.5             | 1.18 | 7.58  |

[*Model with `contrast_weight=1` link*](https://www.aicrowd.com/challenges/trajnet-a-trajectory-forecasting-challenge/submissions/142876).

As we can observe, SGAN generally provides better results than VAE which presents much higher FDE and COL-I scores. In addition to this, our method combining SGAN without the discriminator and contrastive learning (second table) tends to perform better than the standard SGAN available in the TrajNet++ framework. Focusing now only on the second table, it can be seen that the model with `contrast_weight` of 2 presents the smallest COL-I with a value of 6.26. This value is much higher than what we obtained in milestone 2 where we bottomed out at a value of 5.25. Finally, the smallest FDE score we ever achieved with a value of **1.17** has been made possible with the model using the `contrast_weight` parameter of 1.

The commands used to train this model are following:

*Training on real_data*
```
python -m trajnetbaselines.sgan.trainer --path real_data --type social --augment --epochs 15 --d_steps 0 --contrast_weight 1 --save_every 1
```

*Training on synth_data*
```
python -m trajnetbaselines.sgan.trainer --path synth_data --goals --type social --augment --epochs 15 --d_steps 0 --contrast_weight 1 --save_every 1
```

Note that `--d_steps 0` is used to avoid training the discriminator and hence “discard” it.

To assess the validity of the low FDE score we got, we trained our model with `contrast_weight` of 1 on the `five_parallel_synth` dataset for which we have the ground truth. We then run the model and predicted a scene using the built-in visualization feature of TrajNet++. Following figure depicts this scene in which it can be noticed that the predicted trajectory of the primary pedestrian (thick blue line) matches pretty well the ground truth (thick black line).

**Trajectories visualization vs ground truth**

<img src="README_figures/milestone_3/predictions/visualize.scene44977.png">



 
**Training losses visualization**

<img src="README_figures/milestone_3/learning_curves/SGAN-loss-history-different-contrastive-weights.png">

The training curves above represent the loss history of our three SGAN models without discriminator and with contrastive learning. These three models have been trained each time on both real and synthetic data. For instance, “re\_cw\_0.5” corresponds for instance to the model trained on real data with a `contrast_weight` of 0.5. Semi-transparent surfaces show the boundaries of the min and max values of the different losses along the 15 epochs of training. It appears that this gap remains quite large throughout the training. The actual loss curves (colorful lines) are the mean of the losses across the different batches. As seen from the second table above, the best FDE has been obtained with a `contrast_weight` of 1 (orange curve for real_data and violet curve for synth_data). The reason why the model presenting the lowest FDE doesn’t correspond to the lowest loss curve is probably that the loss doesn’t describe the actual performance of the network. In fact, it is difficult to directly deduce the performance of the network only from the aspect of the loss on the various metrics. For instance, a model with a very low FDE and a high COL-I might present a loss curve with higher values than a model with conversely a higher FDE but a lower COL-I. Another intuition we have is that our model with a `contrast_weight` of 0.5 could overfit a bit. This would imply that the training curve appears more promising (i.e. lower in the graph) than its corresponding validation curve (which is here not available). Finally, especially for the very bottom curve, we observe that the loss drops a bit at epoch 10. This is due to the fact that the learning rate scheduler is programmed to reduce the learning rate each 10 epochs. This boosts the learning process and improves slightly the training performance.

**Trajectories visualization**

<img src="README_figures/milestone_3/predictions/sampling_scene_7.png">

The figure above shows trajectory predictions in a scene for our best model (SGAN without discriminator implementing contrastive learning with a `contrast_weight` of 1). Considering the timestamps in the scene, we observe that our model indeed allowed us to avoid unwanted situations such as discomfort zones and collisions with the other agents.

**Results summary**

To summarize this results part, we first used the VAE and SGAN models off the shelf and then implemented our own solution combining SGAN without discriminator and with the contrastive learning introduced in milestone 2. In our approach we have decided to consider predictions with 3 modes, which tends to be a good trade-off between computational cost and taking the advantage of multimodal scenarios. In comparison with the D-LSTM model implementing contrastive learning from milestone 2, SGAN models clearly tend to increase the COL-I and reduce the FDE. To tackle this problem we used contrastive learning, since it was shown in [*Social NCE: Contrastive Learning of Socially-aware Motion Representations*](https://arxiv.org/pdf/2012.11717.pdf) that it helps to provide better results in terms of collision rate without significantly worsening the FDE. Combining both techniques allowed us to stabilize both metrics, ultimately providing one of the highest scores in AICrowd competition.


<a name="mi_3_3"></a>
### 3) Conclusion

The field of autonomous vehicles encompasses various pillars among which “perceiving”, “predicting” and “planning”. Most research has so far focused on “perceiving”. In fact, the “predicting” part (i.e. forecasting) is just as important and crucial for the next step which consists in planning a trajectory. In this project, we have discovered and implemented various methods to tackle the challenges related to pedestrian trajectory forecasting. Those challenges are following:

- Sequence Modelling: Effectively encoding the past trajectories to predict the future trajectories.
- Presence of Social Interactions: The future trajectory of a pedestrian is also affected by motion of surrounding pedestrians.
- Multimodality: Given the past history, multiple futures are plausible.

Thanks to the provided TrajNet++ framework, we began this journey with a simple Vanilla model and ended up with an advanced Social GAN without discriminator able to output multimodal futures and whose loss combines advantages from variety loss and NCE loss. We observed that, combined with a multimodal baseline such as Social GAN, the contrastive learning method is able to promote the social awareness of neural motion models for various possible futures. We hope that the idea of putting these two techniques together can contribute in some way to the development of socially-aware AI.



