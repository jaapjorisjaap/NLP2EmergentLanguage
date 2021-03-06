import pytorch_lightning as pl
import torch
import numpy as np
from torch.nn.utils.rnn import pack_padded_sequence

from attribute_game.utils import pack


class AttributeBaseLineModel(pl.LightningModule):
    def __init__(self, sender, receiver, loss_module,
                 hparams=None, pack_message=False):
        super().__init__()
        self.sender = sender
        self.receiver = receiver

        self.loss_module = loss_module
        self.pack_message = pack_message
        self.msg_len = sender.msg_len
        self.hparams = hparams

    def forward(self, sender_img, receiver_choices):
        msg = self.sender(sender_img)
        if self.pack_message:
            msg_packed = pack(msg, self.msg_len)
            out, out_probs = self.receiver(receiver_choices, msg_packed)
        else:
            msg_packed = None
            out, out_probs = self.receiver(receiver_choices, msg)

        return msg, msg_packed, out, out_probs, None, None

    def training_step(self, batch, batch_idx):
        batch_size = len(batch[0])

        sender_img = batch[0].to(self.device)
        receiver_imgs = batch[1]
        target = batch[2].to(self.device)

        msg, msg_packed, out, out_probs, _, _ = self.forward(sender_img, receiver_imgs)

        loss = self.loss_module(out_probs, target)

        predicted_indices = torch.argmax(out_probs, dim=-1)

        correct = (predicted_indices == target).sum().item() / batch_size

        self.log("loss_receiver", loss, on_step=True, on_epoch=True)
        self.log("total_loss", loss, on_step=True, on_epoch=True)
        self.log("accuracy", correct, on_step=True, on_epoch=True)

        return loss

    @torch.no_grad()
    def validation_step(self, batch, batch_idx):

        self.sender.eval()  # if we do not do this it raises issues for batch normalization
        self.receiver.eval()

        batch_size = len(batch[0])

        sender_img = batch[0].to(self.device)
        receiver_imgs = batch[1]
        target = batch[2].to(self.device)



        msg, msg_packed, out, out_probs, _, _ = self.forward(sender_img, receiver_imgs)

        loss = self.loss_module(out_probs, target)

        predicted_indices = torch.argmax(out_probs, dim=-1)

        correct = (predicted_indices == target).sum().item() / batch_size


        self.log("val_loss_receiver", loss, on_step=True, on_epoch=True)
        self.log("val_total_loss", loss, on_step=True, on_epoch=True)
        self.log("val_accuracy", correct, on_step=True, on_epoch=True)

        self.sender.train()  # make sure to set it back to training
        self.receiver.train()
    def configure_optimizers(self):
        parameters = list(self.sender.parameters()) + list(self.receiver.parameters())

        optimizer = torch.optim.Adam(
            parameters,
            lr=self.hparams['learning_rate'])
        return optimizer


class AttributeModelWithPrediction(pl.LightningModule):
    def __init__(self, sender, receiver, loss_module, predictor, predictor_loss_module,
                 hparams=None, pack_message=True):
        super().__init__()
        self.sender = sender
        self.receiver = receiver
        self.predictor = predictor
        self.loss_module_predictor = predictor_loss_module

        self.loss_module = loss_module
        self.pack_message = pack_message
        self.hparams = hparams

    def forward(self, sender_img, receiver_choices):
        msg = self.sender(sender_img)

        start_symbols = torch.zeros(1, len(sender_img), self.sender.n_symbols).to(self.device)

        msgs = torch.cat([start_symbols, msg], dim=0)

        prediction_logits, prediction_probs, hidden = self.predictor(msgs)

        prediction_logits = prediction_logits[:-1, :, :]
        prediction_probs = prediction_probs[:-1, :, :]

        packed_msg = None
        if self.pack_message:
            packed_msg = pack(msg, self.sender.msg_len)
            out, out_probs = self.receiver(receiver_choices, packed_msg)
        else:
            out, out_probs = self.receiver(receiver_choices, msg)

        return msg, packed_msg, out, out_probs, prediction_logits, prediction_probs

    def training_step(self, batch, batch_idx):
        batch_size = len(batch[0])

        sender_img = batch[0].to(self.device)
        receiver_imgs = batch[1]
        target = batch[2].to(self.device)

        msg, packed_msg, out, out_probs, prediction_logits, prediction_probs = self.forward(sender_img, receiver_imgs)

        ### Get loss of the predictor
        prediction_squeezed = prediction_logits.reshape(-1, self.sender.n_symbols)
        prediction_probs = prediction_probs.reshape(-1, self.sender.n_symbols)
        msg_target = msg.reshape(-1, self.sender.n_symbols)

        loss_predictor = self.loss_module_predictor(prediction_squeezed, msg_target,
                                                    ignore_index=self.sender.n_symbols - 1)
        indices = torch.argmax(msg_target, dim=-1)
        accuracyPredictions = torch.argmax(prediction_probs, dim=-1)

        correct = (accuracyPredictions == indices).sum().item()
        predictor_accuracy = correct / len(indices)
        ### Log the accuracy
        self.log("accuracy predictor", predictor_accuracy, on_step=True, on_epoch=True)

        loss_receiver = self.loss_module(out_probs, target)
        loss = loss_receiver + self.hparams["predictor_loss_weight"] * loss_predictor

        predicted_indices = torch.argmax(out_probs, dim=-1)

        correct = (predicted_indices == target).sum().item() / batch_size

        self.log("loss_predictor", loss_predictor, on_step=True, on_epoch=True)
        self.log("loss_receiver", loss_receiver, on_step=True, on_epoch=True)
        self.log("total_loss", loss, on_step=True, on_epoch=True)
        self.log("accuracy", correct, on_step=True, on_epoch=True)

        return loss



    @torch.no_grad()
    def validation_step(self, batch, batch_idx):

        self.sender.eval()  # if we do not do this it raises issues for batch normalization
        self.receiver.eval()
        self.predictor.eval()

        batch_size = len(batch[0])

        sender_img = batch[0].to(self.device)
        receiver_imgs = batch[1]
        target = batch[2].to(self.device)




        msg, msg_packed, out, out_probs, prediction_logits, prediction_probs = self.forward(sender_img, receiver_imgs)

        ### Get loss of the predictor
        prediction_squeezed = prediction_logits.reshape(-1, self.sender.n_symbols)
        prediction_probs = prediction_probs.reshape(-1, self.sender.n_symbols)
        msg_target = msg.reshape(-1, self.sender.n_symbols)

        loss_predictor = self.loss_module_predictor(prediction_squeezed, msg_target,
                                                    ignore_index=self.sender.n_symbols - 1)
        indices = torch.argmax(msg_target, dim=-1)
        accuracyPredictions = torch.argmax(prediction_probs, dim=-1)

        correct = (accuracyPredictions == indices).sum().item()
        predictor_accuracy = correct / len(indices)
        self.log("val accuracy predictor", predictor_accuracy, on_step=True, on_epoch=True)

        loss_receiver = self.loss_module(out_probs, target)
        loss = loss_receiver + self.hparams["predictor_loss_weight"] * loss_predictor

        predicted_indices = torch.argmax(out_probs, dim=-1)

        correct = (predicted_indices == target).sum().item() / batch_size



        self.log("val_loss_predictor", loss_predictor, on_step=True, on_epoch=True)
        self.log("val_loss_receiver", loss_receiver, on_step=True, on_epoch=True)
        self.log("val_total_loss", loss, on_step=True, on_epoch=True)
        self.log("val_accuracy", correct, on_step=True, on_epoch=True)

        self.sender.train()  # make sure to set it back to training
        self.receiver.train()
        self.predictor.train()


    def configure_optimizers(self):
        parameters = list(self.sender.parameters()) + list(self.receiver.parameters()) + list(
            self.predictor.parameters())

        optimizer = torch.optim.Adam(
            parameters,
            lr=self.hparams['learning_rate'])
        return optimizer


class AttributeModelMerged(pl.LightningModule):
    def __init__(self, sender, receiver, loss_module, predictor, predictor_loss_module,
                 hparams=None):
        super().__init__()
        self.sender = sender
        self.receiver = receiver
        self.predictor = predictor
        self.loss_module_predictor = predictor_loss_module

        self.loss_module = loss_module

        self.hparams = hparams

    def forward(self, sender_img, receiver_choices):
        msg = self.sender(sender_img)

        start_symbols = torch.zeros(1, len(sender_img), self.sender.n_symbols).to(self.device)

        packed_msg = pack(msg, self.sender.msg_len)

        msgs = torch.cat([start_symbols, msg], dim=0)
        msg_in = msgs
        prediction_logits, prediction_probs, hidden = self.predictor(msg_in)

        prediction_logits = prediction_logits[:-1, :, :]
        prediction_probs = prediction_probs[:-1, :, :]

        last_hidden = hidden
        out, out_probs = self.receiver(receiver_choices, last_hidden)

        return msg, packed_msg, out, out_probs, prediction_logits, prediction_probs

    def training_step(self, batch, batch_idx):
        batch_size = len(batch[0])

        sender_img = batch[0].to(self.device)
        receiver_imgs = batch[1]
        target = batch[2].to(self.device)

        msg, out, out_probs, prediction_logits, prediction_probs = self.forward(sender_img, receiver_imgs)

        ### Get loss of the predictor
        prediction_squeezed = prediction_logits.reshape(-1, self.sender.n_symbols)
        prediction_probs = prediction_probs.reshape(-1, self.sender.n_symbols)
        msg_target = msg.reshape(-1, self.sender.n_symbols)

        loss_predictor = self.loss_module_predictor(prediction_squeezed, msg_target)
        indices = torch.argmax(msg_target, dim=-1)
        accuracyPredictions = torch.argmax(prediction_probs, dim=-1)

        correct = (accuracyPredictions == indices).sum().item()
        predictor_accuracy = correct / len(indices)
        ### Log the accuracy
        self.log("accuracy predictor", predictor_accuracy, on_step=True, on_epoch=True)

        loss_receiver = self.loss_module(out_probs, target)
        loss = loss_receiver + self.hparams["predictor_loss_weight"] * loss_predictor

        predicted_indices = torch.argmax(out_probs, dim=-1)

        correct = (predicted_indices == target).sum().item() / batch_size

        self.log("loss_predictor", loss_predictor, on_step=True, on_epoch=True)
        self.log("loss_receiver", loss_receiver, on_step=True, on_epoch=True)
        self.log("total_loss", loss, on_step=True, on_epoch=True)
        self.log("accuracy", correct, on_step=True, on_epoch=True)

        return loss

    def configure_optimizers(self):
        parameters = list(self.sender.parameters()) + list(self.receiver.parameters()) + list(
            self.predictor.parameters())

        optimizer = torch.optim.Adam(
            parameters,
            lr=self.hparams['learning_rate'])
        return optimizer
