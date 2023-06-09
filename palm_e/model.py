import re
from PIL import Image

import torch 
import torch.nn as nn
from torch.optim import Adam
from palm_rlhf_pytorch import PaLM
# import open_clip

from transformers import CLIPModel, AutoTokenizer, CLIPProcessor
import bitsandbytes


from flamingo_pytorch import PerceiverResampler



from embedding import PositionalEmbedding

# vit_model, preprocess_train, preprocess_val = open_clip.create_model_and_transforms('hf-hub:laion/CLIP-ViT-H-14-laion2B-s32B-b79K')
# tokenizer = open_clip.get_tokenizer('hf-hub:laion/CLIP-ViT-H-14-laion2B-s32B-b79K')



# class ViTProjector(nn.Module):
#     def __init__(self, vit_model, input_dim, output_dim):
#         super(ViTProjector, self).__init__()
#         self.vit_model = vit_model
#         self.linear = nn.Linear(input_dim, output_dim)

#     def forward(self, x):
#         encoded_observation = self.vit_model(x)
#         return self.linear(encoded_observation)



class PALME_TOKENIZER:
    def __init__(self):
        self.processor = CLIPProcessor.from_pretrained("laion/CLIP-ViT-L-14-laion2B-s32B-b82K")

        #maybe this will work
        self.tokenizer = AutoTokenizer.from_pretrained(
            "EleutherAI/gpt-neox-20b",
            additional_special_tokens=["<image>", "</image>"],
            extra_ids=0,
            model_max_length=8192
        )


        self.im_idx, self.im_end_idx = self.tokenizer.convert_tokens_to_ids(["<image>", "</image>"])

    def tokenize_texts(self, texts):
        texts =  self.tokenizer(texts, return_tensors="pt", padding=True, truncation=True).input_ids
        # Add image tokens to text as "<s> <image> </image> text </s>"
        image_tokens = torch.tensor([[self.im_idx, self.im_end_idx]] * texts.shape[0])
        return torch.cat([texts[:, 0:1], image_tokens, texts[:, 1:]], dim=1), texts

    def tokenize_images(self, images):
        return self.processor(images=images, return_tensors="pt").pixel_values

    def tokenize(self, sample):
        text_tokens, only_text_tokens = self.tokenize_texts(sample["target_text"])
        attention_mask = text_tokens != self.tokenizer.pad_token_id
        dummy_image_features = torch.ones((text_tokens.shape[0], 64))
        attention_mask = torch.cat([dummy_image_features, attention_mask], dim=1)
        return {
            "text_tokens": text_tokens,
            "images": self.tokenize_images(sample["image"]),
            "labels": only_text_tokens,
            "attention_mask": attention_mask,
        }
    


    
class PALME(nn.Module):
    def __init__(self, LLM,  ViT_model):
        super(PALME, self).__init__()
        self.LLM = LLM
        self.ViT_model = CLIPModel.from_pretrained("laion/CLIP-ViT-L-14-laion2B-s32B-b82K").vision_model

        self.embed = bitsandbytes.nn.modules.Embedding(
            32002,
            2048,
            padding_idx=1
        )

        self.embed_positions= PositionalEmbedding(
            2048,
            2048,
            1
        )

        self.output_projection = torch.nn.Linear(
            2048, 32002, bias=False
        )
        torch.nn.init.normal_(
            self.output_projection.weight, mean=0, std=2048**-0.5
        )

        self.decoder = PaLM(
            num_tokens=50304,
            dim=2048,
            depth=16,
            dim_head=128,
            heads=8,
            flash_attn=True,
            qk_rmsnorm=False,
        )

        #embed tokens
        #embed positions
        #output projections

        self.image_proj = torch.nn.Linear(1024, 2048, bias=False)
        torch.nn.init.normal_(
            self.image_proj.weight, mean=0, std=2048**-0.5
        )


    def forward(self, text_tokens, images, continuous_observations, **kwargs):
        images = self.ViT_model(pixel_values=images)["last_hidden_state"]
        images = self.perceive(images).squeeze(1)
        images = self.image_proj(images)

        model_input = self.decoder.forward_embeddings(text_tokens)[1]
        model_input = torch.cat([model_input[:, 0:2], images, model_input[:, 2:]], dim=1)
        model_input = self.decoder.forward_embedding(model_input, token_embedding=model_input)[0]




