import os, sys, shutil, string, csv, subprocess, logging, random, gc
from datetime import datetime
os.environ['OMP_NUM_THREADS'] = '1'
import numpy as np
import pandas as pd
import nibabel as nib
import tensorflow as tf
import matplotlib.pyplot as plt
from tensorflow import keras
from tensorflow.keras import Model
from tensorflow.keras.utils import Sequence
from tensorflow.keras.models import load_model, save_model

# LOCAL IMPORTS
from ants_CAE import create_convolutional_autoencoder_model_3d
from variational import VariationalLoss, Sampling, VAE, KLAnnealing
from probabalisticVAE import ProbVAE
from utils import exceptions

logger = logging.getLogger(__name__)


class DataGenerator(Sequence):
    
    """
    Custom data generator to handle 4D batches.
    """
    def __init__(self, batch_size, mode, shuffle=True):
        super(DataGenerator, self).__init__()
        self.base_dir = os.getcwd()
        self.input_data_dir = os.path.join(self.base_dir, 'data', 'CN_ABneg')
        self.train_data_dir = os.path.join(self.input_data_dir, 'training')
        self.test_data_dir = os.path.join(self.input_data_dir, 'testing')
        self.mode = mode
        self.data_shape = (180, 220, 180, 1)
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.filenames = self.get_imgs_by_mode()
        self.indexes = list(range(len(self.filenames)))
        self.on_epoch_end()
        if self.shuffle:
            random.shuffle(self.indexes)

    def __len__(self):
        return len(self.filenames)//self.batch_size

    def get_imgs_by_mode(self):
        if self.mode == 'training':
            imgs = os.listdir(self.train_data_dir)
        elif self.mode == 'testing':
            imgs = os.listdir(self.test_data_dir)
        return imgs
    
    def __getitem__(self, index):
        start_idx = index * self.batch_size
        end_idx = (index+1) * self.batch_size
        batch_indexes = self.indexes[start_idx:end_idx]
        batch_filenames = [self.filenames[i] for i in batch_indexes]
        x = np.empty((len(batch_indexes), *self.data_shape))
        #y = np.empty((len(batch_indexes), *self.data_shape))
        for i, idx in enumerate(batch_indexes):
            image = os.path.join(self.input_data_dir, self.mode, batch_filenames[i])
            img = nib.load(image)
            data = img.get_fdata()
            # resize from (182,218,182) --> (180,220,180) for network compatibility
            data = np.pad(data, pad_width=((0,0),(1,1),(0,0)), mode='constant', constant_values=0)
            data = data[1:181,:,1:181]
            # min-max scale data from range [0,255] --> [0,1] for training stability
            data = data/255
            x[i,:,:,:,0] = data
            #y[i,:,:,:,0] = data
        return x

    def get_random_sample(self, n_samples):
        """Use for test data generator only"""
        test_data = self.filenames
        indices = np.random.choice(len(test_data), n_samples, replace=False)
        x = np.empty((n_samples, *self.data_shape))
        for i, idx in enumerate(indices):
            item = test_data[idx]
            item = os.path.join(os.getcwd(), 'data', 'CN_ABneg', 'testing', item)
            img = nib.load(item)
            data = img.get_fdata() 
            # resize from (182,218,182) --> (180,220,180) for network compatibility
            data = np.pad(data, pad_width=((0,0),(1,1),(0,0)), mode='constant', constant_values=0)
            data = data[1:181,:,1:181]
            # min-max scale data from range [0,255] --> [0,1] for training stability
            data = data/255
            x[i,:,:,:,0] = data
        return x
    
    def on_epoch_end(self):
        if self.shuffle:
            random.shuffle(self.indexes)


class AutoencoderTransfer():
    """
    AI model using trained ANTs CAE model for transfer learning
    on custom variational autoencoder
    """

    def __init__(self, batch_size, epochs, fmap_size):
        super(AutoencoderTransfer, self).__init__()
        self.input_shape = (180, 220, 180, 1)
        self.batch_size = batch_size
        self.epochs = epochs
        self.fmap_size = fmap_size
        self.train_data = DataGenerator(batch_size=self.batch_size, mode='training')
        self.test_data = DataGenerator(batch_size=self.batch_size, mode='testing')
        # mixed precision for training trades computation time for memory
        policy = tf.keras.mixed_precision.Policy('mixed_float16')
        tf.keras.mixed_precision.set_global_policy(policy)

    def load_cae_from_file(self, filepath):
        return load_model(filepath, compile=True)        
    
    def build_vae(self):
        """Builds/compiles VAE"""
        vae = VAE(input_shape=self.input_shape, fmap_size=self.fmap_size, kl_weight=1.0)
        vae.build()
        return vae
    
    def get_annealer(self, model, startweight, endweight, n_epochs):
        kl = KLAnnealing(model, kl_start=startweight, kl_end=endweight, annealing_epochs=n_epochs)
        return kl
    
    def transfer_weights(self, cae, vae):
        """Transfers weights from each CAE layer to each VAE layer"""
        enc_layers = cae.layers[:5]
        bottleneck_layer = cae.layers[5]
        dec_layers = cae.layers[5:]
        for i in range(len(vae.encoder.layers)):
            if i < len(enc_layers):
                print(enc_layers[i].name)
                vae.encoder.layers[i].set_weights(enc_layers[i].get_weights())
        for i in range(len(vae.decoder.layers)):
            print('decoding layers', i)
            print(vae.decoder.layers[i].name)
            if i < len(dec_layers) and i > 0:
                print(dec_layers[i].name)
                vae.decoder.layers[i].set_weights(dec_layers[i].get_weights())
    
    def train_model(self, model):
        kl = self.get_annealer(model, 0.001, 1.0, 10)
        model.fit(self.train_data, epochs=self.epochs, callbacks=kl)

    def train_and_save_model(self, model, callbacks=None):
        train_history = model.fit(self.train_data, epochs=self.epochs, callbacks=callbacks)
        return train_history
    
    def test_model(self, model):
        loss = model.evaluate(self.test_data)

    def plot_train_progress(self, train_history):
        pass

    def load_vae_from_file(self, filepath):
        objs = {'VAE': VAE,
                'Sampling': Sampling,
                'VariationalLoss': VariationalLoss,
                'KLAnnealing': KLAnnealing}
        return load_model(filepath, custom_objects=objs, compile=True)
    
    def save_model_to_file(self, model, filepath):
        save_model(model, filepath)
    
    def get_middle_slice(self, image):
        depth = image.shape[2]
        mid_idx = depth//2
        return image[:,:,mid_idx]
    
    def plot_orig_and_recon(self, autoencoder, n_samples, filepath):
        # get sample of reconstructed images
        orig_images = self.test_data.get_random_sample(n_samples)
        recon_images = autoencoder.predict(orig_images)
        # plot original and reconstructed side by side
        fig, axes = plt.subplots(n_samples, 2, figsize=(10, n_samples*3))
        for i in range(n_samples):
            orig_slice = self.get_middle_slice(orig_images[i])
            axes[i,0].imshow(orig_slice, cmap='gray')
            axes[i,0].set_title('Original')
            axes[i,0].axis('off')
            recon_slice = self.get_middle_slice(recon_images[i])
            axes[i,1].imshow(recon_slice, cmap='gray')
            axes[i,1].set_title('Reconstructed')
            axes[i,1].axis('off')
        plt.tight_layout()
        plt.savefig(filepath)


if __name__ == '__main__':
    gc.collect()
    tf.keras.backend.clear_session()
    cae_filepath = os.path.join(os.getcwd(), 'saved_models', 'cae_fmap10_50epochs.keras')
    vae_filepath = os.path.join(os.getcwd(), 'saved_models', 'transfer_vae_fmap10_epochs50.keras')
    vis_filepath = os.path.join(os.getcwd(), 'transfer_vae_img_recon_fmap10_epochs50.png')
    transfer_model = AutoencoderTransfer(batch_size=13, epochs=50, fmap_size=10)
    cae = transfer_model.load_cae_from_file(cae_filepath)
    vae = transfer_model.build_vae()
    transfer_model.transfer_weights(cae, vae)
    print(vae.encoder.summary())
    print(vae.decoder.summary())
    transfer_model.train_model(vae)
    transfer_model.test_model(vae)
    transfer_model.save_model_to_file(vae, filepath=vae_filepath)
    transfer_model.plot_orig_and_recon(vae, n_samples=5, filepath=vis_filepath)

