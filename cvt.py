import torch
from torch import nn
from einops import repeat
from einops.layers.torch import Rearrange
from module_cvt import ConvAttention, PreNorm, FeedForward ,PreNormIm
import numpy as np


class Transformer(nn.Module):
    def __init__(self, dim, depth, heads, dim_head, mlp_dim, dropout=0., last_stage=False):
        super().__init__()
        self.layers = nn.ModuleList([])
        for _ in range(depth):
            self.layers.append(nn.ModuleList([
                PreNormIm(dim, ConvAttention(dim, heads=heads, dim_head=dim_head, dropout=dropout, last_stage=last_stage)),
                PreNorm(dim, FeedForward(dim, mlp_dim, dropout=dropout))
            ]))

    def forward(self, x, image_size):
        for attn, ff in self.layers:
            x = attn(x , image_size) + x
            x = ff(x) + x
        return x




class CvT(nn.Module):
    def __init__(self, in_channels, dim=64, kernels=[7, 3, 3], strides=[4, 2, 2],
                 heads=[1, 3, 6] , depth = [1, 2, 10], pool='cls', dropout=0., emb_dropout=0., scale_dim=4):
        super().__init__()




        assert pool in {'cls', 'mean'}, 'pool type must be either cls (cls token) or mean (mean pooling)'
        self.pool = pool
        self.dim = dim

        ##### Stage 1 #######
        self.stage1_conv_embed = nn.Conv2d(in_channels, dim, kernels[0], strides[0], 2)
        self.ln1 = nn.LayerNorm(dim)
        
        self.stage1_transformer = Transformer(dim=dim, depth=depth[0], heads=heads[0], dim_head=self.dim,
                                              mlp_dim=dim * scale_dim, dropout=dropout)
        


        ##### Stage 2 #######
        in_channels = dim
        scale = heads[1]//heads[0]
        dim = scale*dim
        self.stage2_conv_embed = nn.Sequential(
            nn.Conv2d(in_channels, dim, kernels[1], strides[1], 1),
            
        )
        self.ln2 = nn.LayerNorm(dim)
        
        self.stage2_transformer = Transformer(dim=dim, depth=depth[1], heads=heads[1], dim_head=self.dim,
                                              mlp_dim=dim * scale_dim, dropout=dropout)
     

        ##### Stage 3 #######
        in_channels = dim
        scale = heads[2] // heads[1]
        dim = scale * dim
        self.stage3_conv_embed = nn.Sequential(
            nn.Conv2d(in_channels, dim, kernels[2], strides[2], 1),
            
        )
        
        self.ln3 = nn.LayerNorm(dim)
        
        self.stage3_transformer = Transformer(dim=dim, depth=depth[2], heads=heads[2], dim_head=self.dim,
                                              mlp_dim=dim * scale_dim, dropout=dropout, last_stage=True)
    

        self.cls_token = nn.Parameter(torch.randn(1, 1, dim))
        self.dropout_large = nn.Dropout(emb_dropout)


        self.mlp_head = nn.Sequential(
            nn.LayerNorm(dim),
            
        )
        self.embed_dim = dim

    def forward(self, img):
        image_size = img.shape[-1]
        

        xs = self.stage1_conv_embed(img)
        xs = Rearrange('b c h w -> b (h w) c', h = image_size//4, w = image_size//4)(xs)
        xs = self.ln1(xs)
        xs = self.stage1_transformer(xs , image_size//4)
        xs = Rearrange('b (h w) c -> b c h w', h = image_size//4, w = image_size//4)(xs)


        xs = self.stage2_conv_embed(xs)
        xs = Rearrange('b c h w -> b (h w) c', h = image_size//8, w = image_size//8)(xs)
        xs = self.ln2(xs)
        xs = self.stage2_transformer(xs , image_size//8)
        xs = Rearrange('b (h w) c -> b c h w', h = image_size//8, w = image_size//8)(xs)

        xs = self.stage3_conv_embed(xs)
        xs = Rearrange('b c h w -> b (h w) c', h = image_size//16, w = image_size//16)(xs)
        xs = self.ln3(xs)

        b, n, _ = xs.shape
        cls_tokens = repeat(self.cls_token, '() n d -> b n d', b=b)
        xs = torch.cat((cls_tokens, xs), dim=1)
        xs = self.stage3_transformer(xs , image_size//16)
        xs = xs.mean(dim=1) if self.pool == 'mean' else xs[:, 0]

        xs = self.mlp_head(xs)
        return xs

    
def cvt():
    return CvT(3)
    

if __name__ == "__main__":
    img = torch.ones([1, 3, 224, 224])

    model = CvT(224, 3)

    parameters = filter(lambda p: p.requires_grad, model.parameters())
    parameters = sum([np.prod(p.size()) for p in parameters]) / 1_000_000
    print('Trainable Parameters: %.3fM' % parameters)

    out = model(img)

    print("Shape of out :", out.shape)  # [B, num_classes]