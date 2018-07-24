import json
from os.path import join as join_path

import tensorflow as tf

from constants.app_constants import EMB_DIR
from lib.triplet_loss import batch_hard_triplet_loss
from services.common import make_directory, save_batch_array, tensorflow_debug, use_gpu
from services.logger import Logger

tensorflow_debug(False)
use_gpu(0)

config = tf.ConfigProto()
config.gpu_options.allow_growth = True

HOP = 10
LAYER_1_HIDDEN_UNITS = 512
LAYER_2_HIDDEN_UNITS = 512
LAYER_3_HIDDEN_UNITS = 512
EMBEDDING_SIZE = 512
TRIPLET_MARGIN = 0.2

MODEL_TAG = 'HGRUTRIPLET'

logger = Logger()
logger.set_config(filename='../logs/run-triplet-loss.log', append=True)


class HGRUTripletModel:
    def __init__(self, batch_size, n_features, n_classes):
        self.input_ = tf.placeholder(tf.float32, [batch_size, n_features, None])
        self.labels = tf.placeholder(tf.int32, [batch_size, ])
        self.n_classes = n_classes
        self.lr = tf.Variable(0.0, dtype=tf.float32, trainable=False)

        self.input_ = tf.transpose(self.input_, [0, 2, 1])
        with tf.variable_scope('layer_1'):
            rnn_output, _ = tf.nn.dynamic_rnn(tf.contrib.rnn.GRUCell(LAYER_1_HIDDEN_UNITS), self.input_,
                                              dtype=tf.float32)
            rnn_output = rnn_output[:, ::HOP, :]

        with tf.variable_scope('layer_2'):
            rnn_output, _ = tf.nn.dynamic_rnn(tf.contrib.rnn.GRUCell(LAYER_2_HIDDEN_UNITS), rnn_output,
                                              dtype=tf.float32)
            rnn_output = rnn_output[:, ::HOP, :]

        with tf.variable_scope('layer_3'):
            rnn_output, _ = tf.nn.dynamic_rnn(tf.contrib.rnn.GRUCell(LAYER_3_HIDDEN_UNITS), rnn_output,
                                              dtype=tf.float32)
            rnn_output = tf.reduce_mean(rnn_output, axis=1)

        with tf.variable_scope('layer_4'):
            dense_output = tf.layers.dense(rnn_output, EMBEDDING_SIZE, activation=None)
            self.embeddings = tf.nn.l2_normalize(dense_output, dim=0)

        self.loss = batch_hard_triplet_loss(self.labels, self.embeddings, TRIPLET_MARGIN)
        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.lr).minimize(self.loss)

    def extract(self, save_loc, batch_loader):
        model_loc = join_path(save_loc, 'models')
        save_json = join_path(model_loc, '{}_latest.json'.format(MODEL_TAG))
        with open(save_json, 'r') as f:
            model_json = json.load(f)
        model_path = join_path(model_loc, '{}_Epoch{:d}_Batch{:d}_Loss{:.2f}.ckpt'
                               .format(MODEL_TAG, model_json['e'] + 1, model_json['b'] + 1, model_json['loss']))

        embedding_loc = join_path(save_loc, EMB_DIR)
        make_directory(embedding_loc)

        saver = tf.train.Saver()
        with tf.Session(config=config) as sess:
            print('{}: Restoring Model...'.format(MODEL_TAG))
            saver.restore(sess, model_path)
            for b in range(batch_loader.total_batches()):
                batch_x, args_idx = batch_loader.next()
                print('{}: Extracting Batch {:d} embeddings...'.format(MODEL_TAG, b + 1))
                embeddings = sess.run(self.embeddings, feed_dict={
                    self.input_: batch_x
                })
                save_batch_array(embedding_loc, args_idx, embeddings, ext='.npy')
                print('{}: Saved Batch {:d} embeddings at: {}'.format(MODEL_TAG, b + 1, embedding_loc))

        return batch_loader.get_last_idx()

    def start_train(self, save_loc, batch_loader, epochs, lr, decay):
        save_loc = join_path(save_loc, 'models')
        make_directory(save_loc)
        save_json = join_path(save_loc, '{}_latest.json'.format(MODEL_TAG))

        init = tf.global_variables_initializer()
        saver = tf.train.Saver(tf.global_variables(), max_to_keep=10)
        with tf.Session(config=config) as sess:
            sess.run(init)
            for s in [0, 1, 2]:
                batch_loader.set_split(s)
                for e in range(epochs):
                    current_lr = lr * (decay ** e)
                    for b in range(batch_loader.total_batches()):
                        batch_x, batch_y = batch_loader.next()
                        _, loss = sess.run([self.optimizer, self.loss], feed_dict={
                            self.input_: batch_x,
                            self.labels: batch_y,
                            self.lr: current_lr
                        })
                        logger.info('{}: Epoch {:d} | Batch {:d} | Loss: {:.2f}'.format(MODEL_TAG, e + 1, b + 1, loss))
                        if (e + 1) * (b + 1) % 100 == 0:
                            model_path = join_path(save_loc, '{}_Epoch{:d}_Batch{:d}_Loss{:.2f}.ckpt'
                                                   .format(MODEL_TAG, e + 1, b + 1, loss))
                            model_json = {
                                'e': e,
                                'b': b,
                                's': s,
                                'lr': float(current_lr),
                                'loss': float(loss)
                            }
                            saver.save(sess, model_path)
                            with open(save_json, 'w') as f:
                                f.write(json.dumps(model_json))
                            logger.info('Model Saved at Epoch: {:d}, Batch: {:d} with Loss: {:.2f}'.format(e + 1, b + 1,
                                                                                                           loss))
