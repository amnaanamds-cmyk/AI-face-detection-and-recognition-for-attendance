"""MiniFASNetV2-SE anti-spoofing network with the auxiliary Fourier-spectrum branch.

The architecture is ported from Silent-Face-Anti-Spoofing by Minivision
(https://github.com/minivision-ai/Silent-Face-Anti-Spoofing, src/model_lib/MiniFASNet.py and
MultiFTNet.py), Copyright Minivision, licensed under the Apache License 2.0 - see
LICENSE-Silent-Face-Anti-Spoofing in this folder. Changes: restructured and simplified code,
configurable number of classes and input size, ONNX-friendly flatten. No pretrained weights
from that project are used: the network is trained from scratch on your own data.
"""
from __future__ import annotations

import torch
from torch import nn

# channel widths of the "1.8M_" configuration used by MiniFASNetV2 / V2SE
KEEP = [32, 32, 103, 103, 64, 13, 13, 64, 13, 13, 64, 13,
        13, 64, 13, 13, 64, 231, 231, 128, 231, 231, 128, 52,
        52, 128, 26, 26, 128, 77, 77, 128, 26, 26, 128, 26, 26,
        128, 308, 308, 128, 26, 26, 128, 26, 26, 128, 512, 512]


class ConvBlock(nn.Sequential):
    def __init__(self, cin, cout, kernel=1, stride=1, padding=0, groups=1):
        super().__init__(nn.Conv2d(cin, cout, kernel, stride, padding, groups=groups, bias=False),
                         nn.BatchNorm2d(cout), nn.PReLU(cout))


class LinearBlock(nn.Sequential):
    def __init__(self, cin, cout, kernel=1, stride=1, padding=0, groups=1):
        super().__init__(nn.Conv2d(cin, cout, kernel, stride, padding, groups=groups, bias=False),
                         nn.BatchNorm2d(cout))


class SEModule(nn.Module):
    def __init__(self, channels, reduction=4):
        super().__init__()
        self.gate = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(channels, channels // reduction, 1, bias=False), nn.BatchNorm2d(channels // reduction),
            nn.ReLU(inplace=True),
            nn.Conv2d(channels // reduction, channels, 1, bias=False), nn.BatchNorm2d(channels),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return x * self.gate(x)


class DepthWise(nn.Module):
    """1x1 expand -> 3x3 depth-wise -> 1x1 project, optional residual and squeeze-excitation."""

    def __init__(self, c1, c2, c3, residual=False, stride=1, se=False):
        super().__init__()
        self.conv = ConvBlock(c1[0], c1[1])
        self.conv_dw = ConvBlock(c2[0], c2[1], kernel=3, stride=stride, padding=1, groups=c2[0])
        self.project = LinearBlock(c3[0], c3[1])
        self.residual = residual
        self.se = SEModule(c3[1]) if se and residual else None

    def forward(self, x):
        out = self.project(self.conv_dw(self.conv(x)))
        if self.se is not None:
            out = self.se(out)
        return x + out if self.residual else out


def _stage(k, starts, se_last):
    """Residual stage; `starts` are indexes into KEEP of each block's input width."""
    blocks = []
    for n, i in enumerate(starts):
        blocks.append(DepthWise((k[i], k[i + 1]), (k[i + 1], k[i + 2]), (k[i + 2], k[i + 3]), residual=True,
                                se=se_last and n == len(starts) - 1))
    return nn.Sequential(*blocks)


class MiniFASNetV2SE(nn.Module):
    def __init__(self, num_classes=3, input_size=80, embedding_size=128, drop_p=0.75, se=True):
        super().__init__()
        k = KEEP
        kernel = (input_size + 15) // 16
        self.conv1 = ConvBlock(3, k[0], kernel=3, stride=2, padding=1)
        self.conv2_dw = ConvBlock(k[0], k[1], kernel=3, padding=1, groups=k[1])
        self.conv_23 = DepthWise((k[1], k[2]), (k[2], k[3]), (k[3], k[4]), stride=2)
        self.conv_3 = _stage(k, [4, 7, 10, 13], se)
        self.conv_34 = DepthWise((k[16], k[17]), (k[17], k[18]), (k[18], k[19]), stride=2)
        self.conv_4 = _stage(k, [19, 22, 25, 28, 31, 34], se)
        self.conv_45 = DepthWise((k[37], k[38]), (k[38], k[39]), (k[39], k[40]), stride=2)
        self.conv_5 = _stage(k, [40, 43], se)
        self.conv_6_sep = ConvBlock(k[46], k[47])
        self.conv_6_dw = LinearBlock(k[47], k[48], kernel=kernel, groups=k[48])
        self.linear = nn.Linear(512, embedding_size, bias=False)
        self.bn = nn.BatchNorm1d(embedding_size)
        self.drop = nn.Dropout(drop_p)
        self.prob = nn.Linear(embedding_size, num_classes, bias=False)

    def features(self, x):
        """Output of conv_4 (1/8 resolution, 128 channels) - input of the Fourier branch."""
        x = self.conv_23(self.conv2_dw(self.conv1(x)))
        return self.conv_4(self.conv_34(self.conv_3(x)))

    def head(self, f):
        x = self.conv_6_dw(self.conv_6_sep(self.conv_5(self.conv_45(f))))
        x = torch.flatten(x, 1)
        return self.prob(self.drop(self.bn(self.linear(x))))

    def forward(self, x):
        return self.head(self.features(x))


class FTGenerator(nn.Sequential):
    """Predicts the (log) Fourier spectrum of the input: an auxiliary task that forces the
    network to look at high-frequency artefacts of prints and screens."""

    def __init__(self, in_channels=128):
        super().__init__(
            nn.Conv2d(in_channels, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(inplace=True),
            nn.Conv2d(128, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.Conv2d(64, 1, 3, padding=1), nn.BatchNorm2d(1), nn.ReLU(inplace=True),
        )


class MultiFTNet(nn.Module):
    """Training wrapper: returns (class logits, predicted spectrum) in train mode, logits in eval mode."""

    def __init__(self, num_classes=3, input_size=80):
        super().__init__()
        self.model = MiniFASNetV2SE(num_classes=num_classes, input_size=input_size)
        self.ft_generator = FTGenerator(128)
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.BatchNorm1d)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, std=0.001)

    @staticmethod
    def spectrum_size(input_size: int) -> int:
        return input_size // 8

    def forward(self, x):
        f = self.model.features(x)
        cls = self.model.head(f)
        if self.training:
            return cls, self.ft_generator(f)
        return cls
