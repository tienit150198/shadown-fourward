"""SCHP human-parsing network (ResNet-101 CE2P), plain PyTorch, no custom CUDA ops.

Vendored from CatVTON `model/SCHP/networks/AugmentCE2P.py` + `model/SCHP/utils/transforms.py`, which are
themselves taken from Self-Correction-Human-Parsing (https://github.com/GoGoDuck912/Self-Correction-Human-Parsing,
MIT License, Peike Li) with InPlaceABNSync replaced by BatchNorm2d + LeakyReLU, and transforms from
Microsoft's HRNet code (MIT License, Bin Xiao). Module/attribute names are kept identical so the official
checkpoints (zhengchong/CatVTON/SCHP/*.pth) load unchanged. The unused ASPP module and the unused edge
logits computation are dropped (they do not affect the parsing output).
"""

from __future__ import annotations

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.nn import BatchNorm2d, LeakyReLU
from torch.nn import functional as F

affine_par = True


def conv3x3(in_planes, out_planes, stride=1):
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride, padding=1, bias=False)


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, dilation=1, downsample=None, fist_dilation=1, multi_grid=1):
        super().__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1 = BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=stride,
                               padding=dilation * multi_grid, dilation=dilation * multi_grid, bias=False)
        self.bn2 = BatchNorm2d(planes)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3 = BatchNorm2d(planes * 4)
        self.relu = nn.ReLU(inplace=False)
        self.relu_inplace = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.dilation = dilation
        self.stride = stride

    def forward(self, x):
        residual = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.relu(self.bn2(self.conv2(out)))
        out = self.bn3(self.conv3(out))
        if self.downsample is not None:
            residual = self.downsample(x)
        out = out + residual
        return self.relu_inplace(out)


class PSPModule(nn.Module):
    def __init__(self, features, out_features=512, sizes=(1, 2, 3, 6)):
        super().__init__()
        self.stages = nn.ModuleList([self._make_stage(features, out_features, size) for size in sizes])
        self.bottleneck = nn.Sequential(
            nn.Conv2d(features + len(sizes) * out_features, out_features, kernel_size=3, padding=1, dilation=1,
                      bias=False),
            BatchNorm2d(out_features),
            LeakyReLU(),
        )

    def _make_stage(self, features, out_features, size):
        prior = nn.AdaptiveAvgPool2d(output_size=(size, size))
        conv = nn.Conv2d(features, out_features, kernel_size=1, bias=False)
        return nn.Sequential(prior, conv, BatchNorm2d(out_features), LeakyReLU())

    def forward(self, feats):
        h, w = feats.size(2), feats.size(3)
        priors = [F.interpolate(input=stage(feats), size=(h, w), mode="bilinear", align_corners=True)
                  for stage in self.stages] + [feats]
        return self.bottleneck(torch.cat(priors, 1))


class Edge_Module(nn.Module):
    def __init__(self, in_fea=(256, 512, 1024), mid_fea=256, out_fea=2):
        super().__init__()
        self.conv1 = nn.Sequential(nn.Conv2d(in_fea[0], mid_fea, 1, bias=False), BatchNorm2d(mid_fea), LeakyReLU())
        self.conv2 = nn.Sequential(nn.Conv2d(in_fea[1], mid_fea, 1, bias=False), BatchNorm2d(mid_fea), LeakyReLU())
        self.conv3 = nn.Sequential(nn.Conv2d(in_fea[2], mid_fea, 1, bias=False), BatchNorm2d(mid_fea), LeakyReLU())
        # Edge logits head: present in the checkpoint, its output is not used for parsing.
        self.conv4 = nn.Conv2d(mid_fea, out_fea, kernel_size=3, padding=1, dilation=1, bias=True)

    def forward(self, x1, x2, x3):
        _, _, h, w = x1.size()
        edge1_fea = self.conv1(x1)
        edge2_fea = F.interpolate(self.conv2(x2), size=(h, w), mode="bilinear", align_corners=True)
        edge3_fea = F.interpolate(self.conv3(x3), size=(h, w), mode="bilinear", align_corners=True)
        return torch.cat([edge1_fea, edge2_fea, edge3_fea], dim=1)


class Decoder_Module(nn.Module):
    def __init__(self, num_classes):
        super().__init__()
        self.conv1 = nn.Sequential(nn.Conv2d(512, 256, 1, bias=False), BatchNorm2d(256), LeakyReLU())
        self.conv2 = nn.Sequential(nn.Conv2d(256, 48, 1, bias=False), BatchNorm2d(48), LeakyReLU())
        self.conv3 = nn.Sequential(
            nn.Conv2d(304, 256, 1, bias=False), BatchNorm2d(256), LeakyReLU(),
            nn.Conv2d(256, 256, 1, bias=False), BatchNorm2d(256), LeakyReLU(),
        )

    def forward(self, xt, xl):
        _, _, h, w = xl.size()
        xt = F.interpolate(self.conv1(xt), size=(h, w), mode="bilinear", align_corners=True)
        xl = self.conv2(xl)
        return self.conv3(torch.cat([xt, xl], dim=1))


class ResNet(nn.Module):
    def __init__(self, block, layers, num_classes):
        self.inplanes = 128
        super().__init__()
        self.conv1 = conv3x3(3, 64, stride=2)
        self.bn1 = BatchNorm2d(64)
        self.relu1 = nn.ReLU(inplace=False)
        self.conv2 = conv3x3(64, 64)
        self.bn2 = BatchNorm2d(64)
        self.relu2 = nn.ReLU(inplace=False)
        self.conv3 = conv3x3(64, 128)
        self.bn3 = BatchNorm2d(128)
        self.relu3 = nn.ReLU(inplace=False)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=1, dilation=2, multi_grid=(1, 1, 1))
        self.context_encoding = PSPModule(2048, 512)
        self.edge = Edge_Module()
        self.decoder = Decoder_Module(num_classes)
        self.fushion = nn.Sequential(
            nn.Conv2d(1024, 256, kernel_size=1, padding=0, dilation=1, bias=False),
            BatchNorm2d(256),
            LeakyReLU(),
            nn.Dropout2d(0.1),
            nn.Conv2d(256, num_classes, kernel_size=1, padding=0, dilation=1, bias=True),
        )

    def _make_layer(self, block, planes, blocks, stride=1, dilation=1, multi_grid=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False),
                BatchNorm2d(planes * block.expansion, affine=affine_par),
            )

        def grid(index, grids):
            return grids[index % len(grids)] if isinstance(grids, tuple) else 1

        layers = [block(self.inplanes, planes, stride, dilation=dilation, downsample=downsample,
                        multi_grid=grid(0, multi_grid))]
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, dilation=dilation, multi_grid=grid(i, multi_grid)))
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.relu1(self.bn1(self.conv1(x)))
        x = self.relu2(self.bn2(self.conv2(x)))
        x = self.relu3(self.bn3(self.conv3(x)))
        x = self.maxpool(x)
        x2 = self.layer1(x)
        x3 = self.layer2(x2)
        x4 = self.layer3(x3)
        x5 = self.layer4(x4)
        x = self.context_encoding(x5)
        parsing_fea = self.decoder(x, x2)
        edge_fea = self.edge(x2, x3, x4)
        return self.fushion(torch.cat([parsing_fea, edge_fea], dim=1))


def resnet101(num_classes: int) -> ResNet:
    return ResNet(Bottleneck, [3, 4, 23, 3], num_classes)


# ---------------------------------------------------------------- transforms (HRNet, MIT)

def _get_3rd_point(a, b):
    direct = a - b
    return b + np.array([-direct[1], direct[0]], dtype=np.float32)


def _get_dir(src_point, rot_rad):
    sn, cs = np.sin(rot_rad), np.cos(rot_rad)
    return [src_point[0] * cs - src_point[1] * sn, src_point[0] * sn + src_point[1] * cs]


def get_affine_transform(center, scale, rot, output_size, shift=np.array([0, 0], dtype=np.float32), inv=0):
    if not isinstance(scale, (np.ndarray, list)):
        scale = np.array([scale, scale])
    src_w = scale[0]
    dst_w = output_size[1]
    dst_h = output_size[0]
    rot_rad = np.pi * rot / 180
    src_dir = _get_dir([0, src_w * -0.5], rot_rad)
    dst_dir = np.array([0, (dst_w - 1) * -0.5], np.float32)
    src = np.zeros((3, 2), dtype=np.float32)
    dst = np.zeros((3, 2), dtype=np.float32)
    src[0, :] = center + scale * shift
    src[1, :] = center + src_dir + scale * shift
    dst[0, :] = [(dst_w - 1) * 0.5, (dst_h - 1) * 0.5]
    dst[1, :] = np.array([(dst_w - 1) * 0.5, (dst_h - 1) * 0.5]) + dst_dir
    src[2:, :] = _get_3rd_point(src[0, :], src[1, :])
    dst[2:, :] = _get_3rd_point(dst[0, :], dst[1, :])
    if inv:
        return cv2.getAffineTransform(np.float32(dst), np.float32(src))
    return cv2.getAffineTransform(np.float32(src), np.float32(dst))


def transform_logits(logits, center, scale, width, height, input_size):
    trans = get_affine_transform(center, scale, 0, input_size, inv=1)
    out = [
        cv2.warpAffine(logits[:, :, i], trans, (int(width), int(height)), flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=(0))
        for i in range(logits.shape[2])
    ]
    return np.stack(out, axis=2)
