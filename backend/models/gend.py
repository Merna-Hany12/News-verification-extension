import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from transformers import PretrainedConfig, PreTrainedModel

class LinearProbe(nn.Module):
    def __init__(self, input_dim, num_classes, normalize_inputs=False):
        super().__init__()
        self.linear = nn.Linear(input_dim, num_classes)
        self.normalize_inputs = normalize_inputs

    def forward(self, x: torch.Tensor, **kwargs):
        if self.normalize_inputs:
            x = F.normalize(x, p=2, dim=1)
        return self.linear(x)

class CLIPEncoder(nn.Module):
    def __init__(self, model_name="openai/clip-vit-large-patch14"):
        super().__init__()
        from transformers import CLIPModel, CLIPProcessor
        try:
            self._preprocess = CLIPProcessor.from_pretrained(model_name)
        except Exception:
            self._preprocess = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch16")

        clip: CLIPModel = CLIPModel.from_pretrained(model_name)
        self.vision_model = clip.vision_model
        self.model_name = model_name
        self.features_dim = self.vision_model.config.hidden_size
        self.visual_projection = clip.visual_projection

    def preprocess(self, image: Image.Image) -> torch.Tensor:
        return self._preprocess(images=image, return_tensors="pt")["pixel_values"][0]

    def forward(self, preprocessed_images: torch.Tensor) -> torch.Tensor:
        return self.vision_model(preprocessed_images).pooler_output

    def get_features_dim(self):
        return self.features_dim

class GenDConfig(PretrainedConfig):
    model_type = "GenD"
    def __init__(self, backbone: str = "openai/clip-vit-large-patch14", head: str = "linear", **kwargs):
        super().__init__(**kwargs)
        self.backbone = backbone
        self.head = head

class GenD(PreTrainedModel):
    config_class = GenDConfig
    def __init__(self, config):
        super().__init__(config)
        self.head = config.head
        self.backbone = config.backbone
        self.config = config
        self._init_feature_extractor()
        self._init_head()

    def _init_feature_extractor(self):
        backbone_lowercase = self.backbone.lower()
        if "clip" in backbone_lowercase:
            self.feature_extractor = CLIPEncoder(self.backbone)
        else:
            raise ValueError(f"Unknown or unsupported backbone in this workspace: {self.backbone}")

    def _init_head(self):
        features_dim = self.feature_extractor.get_features_dim()
        if self.head == "linear":
            self.model = LinearProbe(features_dim, 2)
        elif self.head == "LinearNorm":
            self.model = LinearProbe(features_dim, 2, True)
        else:
            raise ValueError(f"Unknown head: {self.head}")

    def forward(self, inputs: torch.Tensor):
        features = self.feature_extractor(inputs)
        outputs = self.model.forward(features)
        return outputs
