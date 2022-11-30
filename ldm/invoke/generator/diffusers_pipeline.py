import secrets
import warnings
from dataclasses import dataclass
from typing import List, Optional, Union, Callable

import torch
from diffusers.models import AutoencoderKL, UNet2DConditionModel
from diffusers.pipeline_utils import DiffusionPipeline
from diffusers.pipelines.stable_diffusion import StableDiffusionPipelineOutput
from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker
from diffusers.schedulers import DDIMScheduler, LMSDiscreteScheduler, PNDMScheduler
from transformers import CLIPFeatureExtractor, CLIPTextModel, CLIPTokenizer


@dataclass
class PipelineIntermediateState:
    run_id: str
    step: int
    timestep: int
    latents: torch.Tensor
    predicted_original: Optional[torch.Tensor] = None


class StableDiffusionGeneratorPipeline(DiffusionPipeline):
    r"""
    Pipeline for text-to-image generation using Stable Diffusion.

    This model inherits from [`DiffusionPipeline`]. Check the superclass documentation for the generic methods the
    library implements for all the pipelines (such as downloading or saving, running on a particular device, etc.)

    Implementation note: This class started as a refactored copy of diffusers.StableDiffusionPipeline.
    Hopefully future versions of diffusers provide access to more of these functions so that we don't
    need to duplicate them here: https://github.com/huggingface/diffusers/issues/551#issuecomment-1281508384

    Args:
        vae ([`AutoencoderKL`]):
            Variational Auto-Encoder (VAE) Model to encode and decode images to and from latent representations.
        text_encoder ([`CLIPTextModel`]):
            Frozen text-encoder. Stable Diffusion uses the text portion of
            [CLIP](https://huggingface.co/docs/transformers/model_doc/clip#transformers.CLIPTextModel), specifically
            the [clip-vit-large-patch14](https://huggingface.co/openai/clip-vit-large-patch14) variant.
        tokenizer (`CLIPTokenizer`):
            Tokenizer of class
            [CLIPTokenizer](https://huggingface.co/docs/transformers/v4.21.0/en/model_doc/clip#transformers.CLIPTokenizer).
        unet ([`UNet2DConditionModel`]): Conditional U-Net architecture to denoise the encoded image latents.
        scheduler ([`SchedulerMixin`]):
            A scheduler to be used in combination with `unet` to denoise the encoded image latens. Can be one of
            [`DDIMScheduler`], [`LMSDiscreteScheduler`], or [`PNDMScheduler`].
        safety_checker ([`StableDiffusionSafetyChecker`]):
            Classification module that estimates whether generated images could be considered offsensive or harmful.
            Please, refer to the [model card](https://huggingface.co/CompVis/stable-diffusion-v1-4) for details.
        feature_extractor ([`CLIPFeatureExtractor`]):
            Model that extracts features from generated images to be used as inputs for the `safety_checker`.
    """

    ID_LENGTH = 8

    def __init__(
        self,
        vae: AutoencoderKL,
        text_encoder: CLIPTextModel,
        tokenizer: CLIPTokenizer,
        unet: UNet2DConditionModel,
        scheduler: Union[DDIMScheduler, PNDMScheduler, LMSDiscreteScheduler],
        safety_checker: StableDiffusionSafetyChecker,
        feature_extractor: CLIPFeatureExtractor,
    ):
        super().__init__()

        self.register_modules(
            vae=vae,
            text_encoder=text_encoder,
            tokenizer=tokenizer,
            unet=unet,
            scheduler=scheduler,
            safety_checker=safety_checker,
            feature_extractor=feature_extractor,
        )

    def enable_attention_slicing(self, slice_size: Optional[Union[str, int]] = "auto"):
        r"""
        Enable sliced attention computation.

        When this option is enabled, the attention module will split the input tensor in slices, to compute attention
        in several steps. This is useful to save some memory in exchange for a small speed decrease.

        Args:
            slice_size (`str` or `int`, *optional*, defaults to `"auto"`):
                When `"auto"`, halves the input to the attention heads, so attention will be computed in two steps. If
                a number is provided, uses as many slices as `attention_head_dim // slice_size`. In this case,
                `attention_head_dim` must be a multiple of `slice_size`.
        """
        if slice_size == "auto":
            # half the attention head size is usually a good trade-off between
            # speed and memory
            slice_size = self.unet.config.attention_head_dim // 2
        self.unet.set_attention_slice(slice_size)

    def disable_attention_slicing(self):
        r"""
        Disable sliced attention computation. If `enable_attention_slicing` was previously invoked, this method will go
        back to computing attention in one step.
        """
        # set slice_size = `None` to disable `attention slicing`
        self.enable_attention_slicing(None)

    def enable_xformers_memory_efficient_attention(self):
        r"""
        Enable memory efficient attention as implemented in xformers.

        When this option is enabled, you should observe lower GPU memory usage and a potential speed up at inference
        time. Speed up at training time is not guaranteed.

        Warning: When Memory Efficient Attention and Sliced attention are both enabled, the Memory Efficient Attention
        is used.
        """
        self.unet.set_use_memory_efficient_attention_xformers(True)

    def disable_xformers_memory_efficient_attention(self):
        r"""
        Disable memory efficient attention as implemented in xformers.
        """
        self.unet.set_use_memory_efficient_attention_xformers(False)

    @torch.no_grad()
    def __call__(
        self,
        prompt: Union[str, List[str]],
        height: Optional[int] = 512,
        width: Optional[int] = 512,
        num_inference_steps: Optional[int] = 50,
        guidance_scale: Optional[float] = 7.5,
        generator: Optional[torch.Generator] = None,
        latents: Optional[torch.FloatTensor] = None,
        callback: Optional[Callable[[PipelineIntermediateState], None]] = None,
        **extra_step_kwargs,
    ):
        r"""
        Function invoked when calling the pipeline for generation.

        Args:
            prompt (`str` or `List[str]`):
                The prompt or prompts to guide the image generation.
            height (`int`, *optional*, defaults to 512):
                The height in pixels of the generated image.
            width (`int`, *optional*, defaults to 512):
                The width in pixels of the generated image.
            num_inference_steps (`int`, *optional*, defaults to 50):
                The number of denoising steps. More denoising steps usually lead to a higher quality image at the
                expense of slower inference.
            guidance_scale (`float`, *optional*, defaults to 7.5):
                Guidance scale as defined in [Classifier-Free Diffusion Guidance](https://arxiv.org/abs/2207.12598).
                `guidance_scale` is defined as `w` of equation 2. of [Imagen
                Paper](https://arxiv.org/pdf/2205.11487.pdf). Guidance scale is enabled by setting `guidance_scale >
                1`. Higher guidance scale encourages to generate images that are closely linked to the text `prompt`,
                usually at the expense of lower image quality.
            generator (`torch.Generator`, *optional*):
                A [torch generator](https://pytorch.org/docs/stable/generated/torch.Generator.html) to make generation
                deterministic.
            latents (`torch.FloatTensor`, *optional*):
                Pre-generated noisy latents, sampled from a Gaussian distribution, to be used as inputs for image
                generation. Can be used to tweak the same generation with different prompts. If not provided, a latents
                tensor will ge generated by sampling using the supplied random `generator`.

        Returns:
            [`~pipelines.stable_diffusion.StableDiffusionPipelineOutput`] or `tuple`:
            [`~pipelines.stable_diffusion.StableDiffusionPipelineOutput`] if `return_dict` is True, otherwise a `tuple.
            When returning a tuple, the first element is a list with the generated images, and the second element is a
            list of `bool`s denoting whether the corresponding generated image likely represents "not-safe-for-work"
            (nsfw) content, according to the `safety_checker`.
        """
        result = None
        for result in self.generate(
                prompt, height=height, width=width, num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale, generator=generator, latents=latents,
                **extra_step_kwargs):
            if callback is not None:
                callback(result)
        if result is None:
            raise AssertionError("why was that an empty generator?")
        return result

    def image_from_embeddings(self, latents: torch.Tensor, num_inference_steps: int,
                              text_embeddings: torch.Tensor, guidance_scale: float,
                              *, callback: Callable[[PipelineIntermediateState], None]=None, run_id=None,
                              **extra_step_kwargs) -> StableDiffusionPipelineOutput:
        self.scheduler.set_timesteps(num_inference_steps)
        result = None
        for result in self.generate_from_embeddings(
                latents, text_embeddings, guidance_scale, run_id, **extra_step_kwargs):
            if callback is not None and isinstance(result, PipelineIntermediateState):
                callback(result)
        if result is None:
            raise AssertionError("why was that an empty generator?")
        return result

    def generate(
        self,
        prompt: Union[str, List[str]],
        *,
        opposing_prompt: Union[str, List[str]] = None,
        height: Optional[int] = 512,
        width: Optional[int] = 512,
        num_inference_steps: Optional[int] = 50,
        guidance_scale: Optional[float] = 7.5,
        generator: Optional[torch.Generator] = None,
        latents: Optional[torch.FloatTensor] = None,
        run_id: str = None,
        **extra_step_kwargs,
    ):
        if isinstance(prompt, str):
            batch_size = 1
        else:
            batch_size = len(prompt)

        if height % 8 != 0 or width % 8 != 0:
            raise ValueError(f"`height` and `width` have to be divisible by 8 but are {height} and {width}.")

        # here `guidance_scale` is defined analog to the guidance weight `w` of equation (2)
        # of the Imagen paper: https://arxiv.org/pdf/2205.11487.pdf . `guidance_scale = 1`
        # corresponds to doing no classifier free guidance.
        do_classifier_free_guidance = guidance_scale > 1.0
        text_embeddings = self.get_text_embeddings(prompt, opposing_prompt, do_classifier_free_guidance, batch_size)\
            .to(self.unet.device)
        self.scheduler.set_timesteps(num_inference_steps)
        latents = self.prepare_latents(latents, batch_size, height, width, generator, self.unet.dtype)

        yield from self.generate_from_embeddings(latents, text_embeddings, guidance_scale, run_id, **extra_step_kwargs)

    def generate_from_embeddings(self, latents: torch.Tensor, text_embeddings: torch.Tensor, guidance_scale: float,
                                 run_id: str = None, **extra_step_kwargs):
        if run_id is None:
            run_id = secrets.token_urlsafe(self.ID_LENGTH)
        # scale the initial noise by the standard deviation required by the scheduler
        latents *= self.scheduler.init_noise_sigma
        yield PipelineIntermediateState(run_id=run_id, step=-1, timestep=self.scheduler.num_train_timesteps,
                                        latents=latents)
        # NOTE: Depends on scheduler being already initialized!
        for i, t in enumerate(self.progress_bar(self.scheduler.timesteps)):
            step_output = self.step(t, latents, guidance_scale, text_embeddings, **extra_step_kwargs)
            latents = step_output.prev_sample
            predicted_original = getattr(step_output, 'pred_original_sample', None)
            yield PipelineIntermediateState(run_id=run_id, step=i, timestep=int(t), latents=latents,
                                            predicted_original=predicted_original)

        # https://discuss.huggingface.co/t/memory-usage-by-later-pipeline-stages/23699
        torch.cuda.empty_cache()

        image = self.decode_to_image(latents)
        output = StableDiffusionPipelineOutput(images=image, nsfw_content_detected=[])
        yield self.check_for_safety(output)

    @torch.inference_mode()
    def step(self, t, latents: torch.Tensor, guidance_scale, text_embeddings: torch.Tensor, **extra_step_kwargs):
        do_classifier_free_guidance = guidance_scale > 1.0

        # expand the latents if we are doing classifier free guidance
        latent_model_input = torch.cat([latents] * 2) if do_classifier_free_guidance else latents
        latent_model_input = self.scheduler.scale_model_input(latent_model_input, t)

        # predict the noise residual
        noise_pred = self.unet(latent_model_input, t, encoder_hidden_states=text_embeddings).sample

        # perform guidance
        if do_classifier_free_guidance:
            noise_pred_uncond, noise_pred_text = noise_pred.chunk(2)
            noise_pred = noise_pred_uncond + guidance_scale * (noise_pred_text - noise_pred_uncond)

        # compute the previous noisy sample x_t -> x_t-1
        return self.scheduler.step(noise_pred, t, latents, **extra_step_kwargs)

    @torch.inference_mode()
    def check_for_safety(self, output):
        if not getattr(self, 'feature_extractor') or not getattr(self, 'safety_checker'):
            return output
        images = output.images
        safety_checker_output = self.feature_extractor(self.numpy_to_pil(images),
                                                       return_tensors="pt").to(self.device)
        screened_images, has_nsfw_concept = self.safety_checker(
            images=images, clip_input=safety_checker_output.pixel_values)
        return StableDiffusionPipelineOutput(screened_images, has_nsfw_concept)

    @torch.inference_mode()
    def decode_to_image(self, latents):
        # scale and decode the image latents with vae
        latents = 1 / 0.18215 * latents
        image = self.vae.decode(latents).sample
        image = (image / 2 + 0.5).clamp(0, 1)
        image = image.cpu().permute(0, 2, 3, 1).numpy()
        return image

    @torch.inference_mode()
    def get_text_embeddings(self,
                            prompt: Union[str, List[str]],
                            opposing_prompt: Union[str, List[str]],
                            do_classifier_free_guidance: bool,
                            batch_size: int):
        # get prompt text embeddings
        text_input = self._tokenize(prompt)

        text_embeddings = self.text_encoder(text_input.input_ids)[0]
        # get unconditional embeddings for classifier free guidance
        if do_classifier_free_guidance:
            # opposing prompt defaults to blank caption for everything in the batch
            text_anti_input = self._tokenize(opposing_prompt or [""] * batch_size)
            uncond_embeddings = self.text_encoder(text_anti_input.input_ids)[0]

            # For classifier free guidance, we need to do two forward passes.
            # Here we concatenate the unconditional and text embeddings into a single batch
            # to avoid doing two forward passes
            # FIXME: assert these two are the same size
            text_embeddings = torch.cat([uncond_embeddings, text_embeddings])
        return text_embeddings

    def get_learned_conditioning(self, c: List[List[str]], return_tokens=True,
                                 fragment_weights=None, **kwargs):
        """
        Compatibility function for ldm.models.diffusion.ddpm.LatentDiffusion.
        """
        assert return_tokens == True
        if fragment_weights:
            weights = fragment_weights[0]
            if any(weight != 1.0 for weight in weights):
                warnings.warn(f"fragment weights not implemented yet {fragment_weights}", stacklevel=2)

        if kwargs:
            warnings.warn(f"unsupported args {kwargs}", stacklevel=2)

        text_fragments = c[0]
        text_input = self._tokenize(text_fragments)

        with torch.inference_mode():
            token_ids = text_input.input_ids.to(self.text_encoder.device)
            text_embeddings = self.text_encoder(token_ids)[0]
        return text_embeddings, text_input.input_ids

    @torch.inference_mode()
    def _tokenize(self, prompt: Union[str, List[str]]):
        return self.tokenizer(
            prompt,
            padding="max_length",
            max_length=self.tokenizer.model_max_length,
            truncation=True,
            return_tensors="pt",
        )

    @property
    def channels(self) -> int:
        """Compatible with DiffusionWrapper"""
        return self.unet.in_channels

    def prepare_latents(self, latents, batch_size, height, width, generator, dtype):
        # get the initial random noise unless the user supplied it
        # Unlike in other pipelines, latents need to be generated in the target device
        # for 1-to-1 results reproducibility with the CompVis implementation.
        # However this currently doesn't work in `mps`.
        latents_shape = (batch_size, self.unet.in_channels, height // 8, width // 8)
        if latents is None:
            latents = torch.randn(
                latents_shape,
                generator=generator,
                device=self.unet.device,
                dtype=dtype
            )
        else:
            if latents.shape != latents_shape:
                raise ValueError(
                    f"Unexpected latents shape, got {latents.shape}, expected {latents_shape}")
        if latents.device != self.unet.device:
            raise ValueError(f"Unexpected latents device, got {latents.device}, "
                             f"expected {self.unet.device}")

        # scale the initial noise by the standard deviation required by the scheduler
        latents *= self.scheduler.init_noise_sigma
        return latents
