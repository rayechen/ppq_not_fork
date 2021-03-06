import os
from typing import Any, Callable, List

import torch
from ppq.core import (NetworkFramework, TargetPlatform, empty_ppq_cache,
                      ppq_warning)
from ppq.executor import TorchExecutor
from ppq.IR import (BaseGraph, GraphCommand, GraphCommandType, GraphFormatter,
                    GraphMerger)
from ppq.IR.morph import GraphDeviceSwitcher
from ppq.parser import *
from ppq.quantization.quantizer import (ACADEMIC_INT4_Quantizer,
                                        ACADEMIC_Mix_Quantizer,
                                        ACADEMICQuantizer, BaseQuantizer,
                                        ExtQuantizer,
                                        MetaxChannelwiseQuantizer,
                                        MetaxTensorwiseQuantizer,
                                        NXP_Quantizer, ORT_PerChannelQuantizer,
                                        ORT_PerTensorQuantizer,
                                        PPL_DSP_Quantizer,
                                        PPL_DSP_TI_Quantizer,
                                        PPLCUDA_INT4_Quantizer,
                                        PPLCUDAMixPrecisionQuantizer,
                                        PPLCUDAQuantizer, TensorRTQuantizer)
from ppq.scheduler import DISPATCHER_TABLE, GraphDispatcher
from torch.utils.data import DataLoader

from .setting import *

QUANTIZER_COLLECTION = {
    TargetPlatform.PPL_DSP_INT8: PPL_DSP_Quantizer,
    TargetPlatform.PPL_DSP_TI_IN8: PPL_DSP_TI_Quantizer,
    TargetPlatform.SNPE_INT8:    PPL_DSP_Quantizer,
    TargetPlatform.TRT_INT8:     TensorRTQuantizer,
    TargetPlatform.NXP_INT8:     NXP_Quantizer,
    TargetPlatform.ORT_OOS_INT8: ORT_PerTensorQuantizer,
    TargetPlatform.METAX_INT8_C: MetaxChannelwiseQuantizer,
    TargetPlatform.METAX_INT8_T: MetaxTensorwiseQuantizer,
    # TargetPlatform.ORT_OOS_INT8: ORT_PerChannelQuantizer,
    TargetPlatform.PPL_CUDA_INT8: PPLCUDAQuantizer,
    TargetPlatform.EXTENSION:     ExtQuantizer,
    TargetPlatform.PPL_CUDA_MIX:  PPLCUDAMixPrecisionQuantizer,
    TargetPlatform.PPL_CUDA_INT4: PPLCUDA_INT4_Quantizer,
    TargetPlatform.ACADEMIC_INT8: ACADEMICQuantizer,
    TargetPlatform.ACADEMIC_INT4: ACADEMIC_INT4_Quantizer,
    TargetPlatform.ACADEMIC_MIX:  ACADEMIC_Mix_Quantizer
}

PARSERS = {
    NetworkFramework.ONNX: OnnxParser,
    NetworkFramework.CAFFE: CaffeParser,
    NetworkFramework.NATIVE: NativeImporter
}

EXPORTERS = {
    TargetPlatform.PPL_DSP_INT8:  PPLDSPCaffeExporter,
    TargetPlatform.PPL_DSP_TI_IN8: PPLDSPTICaffeExporter,
    TargetPlatform.PPL_CUDA_INT8: PPLBackendExporter,
    TargetPlatform.SNPE_INT8:     SNPECaffeExporter,
    TargetPlatform.NXP_INT8:      NxpExporter,
    TargetPlatform.ONNX:          OnnxExporter,
    TargetPlatform.ONNXRUNTIME:   ONNXRUNTIMExporter,
    TargetPlatform.CAFFE:         CaffeExporter,
    TargetPlatform.NATIVE:        NativeExporter,
    TargetPlatform.EXTENSION:     ExtensionExporter,
    # TargetPlatform.ORT_OOS_INT8:  ONNXRUNTIMExporter,
    TargetPlatform.ORT_OOS_INT8:  ORTOOSExporter,
    TargetPlatform.METAX_INT8_C:  ONNXRUNTIMExporter,
    TargetPlatform.METAX_INT8_T:  ONNXRUNTIMExporter,
}

# ????????????????????????????????????????????????
# postfix for exporting model
EXPORTING_POSTFIX = {
    TargetPlatform.PPL_DSP_INT8:  '.caffemodel',
    TargetPlatform.PPL_DSP_TI_IN8:'.caffemodel',
    TargetPlatform.PPL_CUDA_INT8: '.onnx',
    TargetPlatform.SNPE_INT8:     '.caffemodel',
    TargetPlatform.NXP_INT8:      '.caffemodel',
    TargetPlatform.ONNX:          '.onnx',
    TargetPlatform.ONNXRUNTIME:   '.onnx',
    TargetPlatform.CAFFE:         '.caffemodel',
    TargetPlatform.NATIVE:        '.native',
    TargetPlatform.EXTENSION:     '.ext',
    TargetPlatform.ORT_OOS_INT8:  '.onnx',
    TargetPlatform.METAX_INT8_C:  '.onnx',
    TargetPlatform.METAX_INT8_T:  '.onnx',
}

def load_graph(file_path: str, from_framework: NetworkFramework=NetworkFramework.ONNX, **kwargs) -> BaseGraph:
    if from_framework not in PARSERS:
        raise KeyError(f'Requiring framework {from_framework} does not support parsing now.')
    parser = PARSERS[from_framework]()
    assert isinstance(parser, GraphBuilder), 'Unexpected Parser found.'
    if from_framework == NetworkFramework.CAFFE:
        assert 'caffemodel_path' in kwargs, ('parameter "caffemodel_path" is required here for loading caffe model from file, '
                                             'however it is missing from your invoking.')
        graph = parser.build(prototxt_path=file_path, caffemodel_path=kwargs['caffemodel_path'])
    else:
        graph = parser.build(file_path)
    return graph

def load_onnx_graph(onnx_import_file: str) -> BaseGraph:
    """
        ??????????????????????????? onnx ??????????????????????????????????????????????????????????????????????????????????????????????????????
        load onnx graph from the specified location
    Args:
        onnx_import_file (str): onnx ???????????????????????? the specified location

    Returns:
        BaseGraph: ?????? onnx ????????? ppq ??????????????? the parsed ppq IR graph
    """
    ppq_ir = load_graph(onnx_import_file, from_framework=NetworkFramework.ONNX)
    return format_graph(graph=ppq_ir)

def load_caffe_graph(prototxt_path: str, caffemodel_path: str) -> BaseGraph:
    """
        ??????????????????????????? caffe ??????????????????????????????????????????????????????????????????????????????????????????????????????
        load caffe graph from the specified location
    Args:
        prototxt_path (str): caffe prototxt??????????????? the specified location of caffe prototxt
        caffemodel_path (str): caffe weight??????????????? the specified lcoation of caffe weight

    Returns:
        BaseGraph: ?????? caffe ????????? ppq ??????????????? the parsed ppq IR graph
    """
    ppq_ir = load_graph(file_path=prototxt_path, caffemodel_path=caffemodel_path, from_framework=NetworkFramework.CAFFE)
    return format_graph(graph=ppq_ir)

def dump_torch_to_onnx(
    model: torch.nn.Module, 
    onnx_export_file: str, 
    input_shape: List[int], 
    input_dtype: torch.dtype, 
    inputs: List[Any] = None,
    device: str = 'cuda'):
    """
        ???????????? torch ????????? onnx???????????????????????????
        convert a torch model to onnx and save to the specified location
    Args:
        model (torch.nn.Module): ???????????? torch ?????? torch model used for conversion

        onnx_export_file (str): ????????????????????? the path to save onnx model

        input_shape (List[int]): ????????????????????????????????? jit.trace??????????????????????????????????????????????????????????????????????????????
            ???????????????????????????????????????????????? inputs ???????????????????????????????????? None
            a list of ints indicating size of input, for multiple inputs, please use keyword arg inputs for 
            direct parameter passing and this should be set to None 

        input_dtype (torch.dtype): ??????????????????????????????????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                   the torch datatype of input, for multiple inputs, please use keyword arg inputs
                                   for direct parameter passing and this should be set to None

        inputs (List[Any], optional): ???????????????????????????????????????Inputs???????????????????????????List????????????????????????tracing???
                                    for multiple inputs, please give the specified inputs directly in the form of
                                    a list of arrays

        device (str, optional): ??????????????????????????? the execution device, defaults to 'cuda'.
    """

    # set model to eval mode, stablize normalization weights.
    assert isinstance(model, torch.nn.Module), (
        f'Model must be instance of torch.nn.Module, however {type(model)} is given.')
    model.eval()

    if inputs is None:
        dummy_input = torch.zeros(size=input_shape, device=device, dtype=input_dtype)
    else: dummy_input = inputs

    torch.onnx.export(
        model=model, args=dummy_input,
        verbose=False, f=onnx_export_file, opset_version=11,
    )

@ empty_ppq_cache
def quantize_onnx_model(
    onnx_import_file: str,
    calib_dataloader: DataLoader,
    calib_steps: int,
    input_shape: List[int],
    input_dtype: torch.dtype = torch.float,
    inputs: List[Any] = None,
    setting: QuantizationSetting = None,
    collate_fn: Callable = None,
    platform: TargetPlatform = TargetPlatform.PPL_DSP_INT8,
    device: str = 'cuda',
    verbose: int = 0,
    do_quantize: bool = True,
) -> BaseGraph:
    """
        ???????????? onnx ???????????????
            ???????????? onnx ?????????????????????
            ???????????????????????? PPQ.IR.BaseGraph
        quantize onnx model, input onnx model and return quantized ppq IR graph

    Args:
        onnx_import_file (str): ???????????? onnx ?????????????????? onnx model location
 
        calib_dataloader (DataLoader): ??????????????? calibration data loader

        calib_steps (int): ???????????? calibration steps

        collate_fn (Callable): ?????????????????????????????? batch collate func for preprocessing
        
        input_shape (List[int]): ????????????????????????????????? jit.trace??????????????????????????????????????????????????????????????????????????????
            ???????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                a list of ints indicating size of input, for multiple inputs, please use 
                                keyword arg inputs for direct parameter passing and this should be set to None

        input_dtype (torch.dtype): ??????????????????????????????????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                the torch datatype of input, for multiple inputs, please use keyword arg inputs
                                for direct parameter passing and this should be set to None

        inputs (List[Any], optional): ???????????????????????????????????????Inputs???????????????????????????List????????????????????????tracing???
                                for multiple inputs, please give the specified inputs directly in the form of
                                a list of arrays

        setting (OptimSetting): ?????????????????????????????????????????????????????????????????? None ????????????????????????
                                Quantization setting, default setting will be used when set None

        do_quantize (Bool, optional): ?????????????????? whether to quantize the model, defaults to True.


        platform (TargetPlatform, optional): ????????????????????? target backend platform, defaults to TargetPlatform.DSP_INT8.
                                        
        device (str, optional): ??????????????????????????? execution device, defaults to 'cuda'.

        verbose (int, optional): ???????????????????????? whether to print details, defaults to 0.

    Raises:
        ValueError: ???????????????????????? the given platform doesn't support quantization
        KeyError: ???????????????????????? the given platform is not supported yet

    Returns:
        BaseGraph: ????????????IR????????????????????????????????????????????? 
                   The quantized IR, containing all information needed for backend execution
    """
    if not TargetPlatform.is_quantized_platform(platform=platform):
        raise ValueError(f'Target Platform {platform} is an non-quantable platform.')
    if platform not in QUANTIZER_COLLECTION:
        raise KeyError(f'Target Platform {platform} is not supported by ppq right now.')
    if do_quantize:
        if calib_dataloader is None or calib_steps is None:
            raise TypeError('Quantization needs a valid calib_dataloader and calib_steps setting.')

    if setting is None:
        setting = QuantizationSettingFactory.default_setting()

    ppq_ir = load_onnx_graph(onnx_import_file=onnx_import_file)
    ppq_ir = dispatch_graph(graph=ppq_ir, platform=platform, setting=setting)

    if inputs is None:
        dummy_input = torch.zeros(size=input_shape, device=device, dtype=input_dtype)
    else: dummy_input = inputs

    quantizer = QUANTIZER_COLLECTION[platform](graph=ppq_ir)

    assert isinstance(quantizer, BaseQuantizer)
    executor = TorchExecutor(graph=quantizer._graph, device=device)
    if do_quantize:
        quantizer.quantize(
            inputs=dummy_input,
            calib_dataloader=calib_dataloader,
            executor=executor,
            setting=setting,
            calib_steps=calib_steps,
            collate_fn=collate_fn
        )
        if verbose: quantizer.report()
        return quantizer._graph
    else:
        return quantizer._graph

@ empty_ppq_cache
def quantize_torch_model(
    model: torch.nn.Module,
    calib_dataloader: DataLoader,
    calib_steps: int,
    input_shape: List[int],
    input_dtype: torch.dtype = torch.float,
    setting: QuantizationSetting = None,
    collate_fn: Callable = None,
    inputs: List[Any] = None,
    do_quantize: bool = True,
    platform: TargetPlatform = TargetPlatform.PPL_DSP_INT8,
    onnx_export_file: str = 'onnx.model',
    device: str = 'cuda',
    verbose: int = 0,
    ) -> BaseGraph:
    """
        ???????????? Pytorch ???????????????
            ???????????? torch.nn.Module
            ???????????????????????? PPQ.IR.BaseGraph
        
        quantize a pytorch model, input pytorch model and return quantized ppq IR graph
    Args:
        model (torch.nn.Module): ???????????? torch ??????(torch.nn.Module) the pytorch model

        calib_dataloader (DataLoader): ??????????????? calibration dataloader

        calib_steps (int): ???????????? calibration steps

        collate_fn (Callable): ?????????????????????????????? batch collate func for preprocessing
        
        input_shape (List[int]): ????????????????????????????????? jit.trace??????????????????????????????????????????????????????????????????????????????
            ???????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                a list of ints indicating size of input, for multiple inputs, please use 
                                keyword arg inputs for direct parameter passing and this should be set to None

        input_dtype (torch.dtype): ??????????????????????????????????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                the torch datatype of input, for multiple inputs, please use keyword arg inputs
                                for direct parameter passing and this should be set to None

        setting (OptimSetting): ?????????????????????????????????????????????????????????????????? None ????????????????????????
                                Quantization setting, default setting will be used when set None

        inputs (List[Any], optional): ???????????????????????????????????????Inputs???????????????????????????List????????????????????????tracing???
                                for multiple inputs, please give the specified inputs directly in the form of
                                a list of arrays

        do_quantize (Bool, optional): ?????????????????? whether to quantize the model, defaults to True, defaults to True.

        platform (TargetPlatform, optional): ????????????????????? target backend platform, defaults to TargetPlatform.DSP_INT8.
                                        
        device (str, optional): ??????????????????????????? execution device, defaults to 'cuda'.

        verbose (int, optional): ???????????????????????? whether to print details, defaults to 0.

    Raises:
        ValueError: ???????????????????????? the given platform doesn't support quantization
        KeyError: ???????????????????????? the given platform is not supported yet

    Returns:
        BaseGraph: ????????????IR????????????????????????????????????????????? 
                   The quantized IR, containing all information needed for backend execution
    """
    # dump pytorch model to onnx
    dump_torch_to_onnx(model=model, onnx_export_file=onnx_export_file, 
        input_shape=input_shape, input_dtype=input_dtype, 
        inputs=inputs, device=device)

    return quantize_onnx_model(onnx_import_file=onnx_export_file, 
        calib_dataloader=calib_dataloader, calib_steps=calib_steps, collate_fn=collate_fn, 
        input_shape=input_shape, input_dtype=input_dtype, inputs=inputs, setting=setting, 
        platform=platform, device=device, verbose=verbose, do_quantize=do_quantize)

@ empty_ppq_cache
def quantize_caffe_model(
    caffe_proto_file: str,
    caffe_model_file: str,
    calib_dataloader: DataLoader,
    calib_steps: int,
    input_shape: List[int],
    input_dtype: torch.dtype = torch.float,
    setting: QuantizationSetting = None,
    collate_fn: Callable = None,
    inputs: List[Any] = None,
    do_quantize: bool = True,
    platform: TargetPlatform = TargetPlatform.PPL_DSP_INT8,
    device: str = 'cuda',
    verbose: int = 0,
) -> BaseGraph:
    """
        ???????????? caffe ???????????????
            ???????????? caffe ????????????????????????????????????
            ???????????????????????? PPQ.IR.BaseGraph
        quantize caffe model, input caffe prototxt and weight path, return a quantized ppq graph
    Args:
        caffe_proto_file (str): ???????????? caffe ???????????? .prototxt ??????
                                caffe prototxt location

        caffe_model_file (str): ???????????? caffe ???????????? .caffemodel ??????
                                caffe weight location

        calib_dataloader (DataLoader): ??????????????? calibration data loader

        calib_steps (int): ???????????? calibration steps

        collate_fn (Callable): ?????????????????????????????? batch collate func for preprocessing

        input_shape (List[int]): ????????????????????????????????? jit.trace??????????????????????????????????????????????????????????????????????????????
            ???????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                a list of ints indicating size of input, for multiple inputs, please use 
                                keyword arg inputs for direct parameter passing and this should be set to None

        input_dtype (torch.dtype): ??????????????????????????????????????????????????????????????????????????? inputs ???????????????????????????????????? None
                                the torch datatype of input, for multiple inputs, please use keyword arg inputs
                                for direct parameter passing and this should be set to None

        setting (OptimSetting): ?????????????????????????????????????????????????????????????????? None ????????????????????????
                                Quantization setting, default setting will be used when set None

        inputs (List[Any], optional): ???????????????????????????????????????Inputs???????????????????????????List????????????????????????tracing???
                                for multiple inputs, please give the specified inputs directly in the form of
                                a list of arrays

        do_quantize (Bool, optional): ?????????????????? whether to quantize the model, defaults to True, defaults to True.

        platform (TargetPlatform, optional): ????????????????????? target backend platform, defaults to TargetPlatform.DSP_INT8.
                                        
        device (str, optional): ??????????????????????????? execution device, defaults to 'cuda'.

        verbose (int, optional): ???????????????????????? whether to print details, defaults to 0.

    Raises:
        ValueError: ???????????????????????? the given platform doesn't support quantization
        KeyError: ???????????????????????? the given platform is not supported yet

    Returns:
        BaseGraph: ????????????IR????????????????????????????????????????????? 
                   The quantized IR, containing all information needed for backend execution
    """
    if not TargetPlatform.is_quantized_platform(platform=platform):
        raise ValueError(f'Target Platform {platform} is an non-quantable platform.')
    if platform not in QUANTIZER_COLLECTION:
        raise KeyError(f'Target Platform {platform} is not supported by ppq right now.')
    if do_quantize:
        if calib_dataloader is None or calib_steps is None:
            raise TypeError('Quantization needs a valid calib_dataloader and calib_steps setting.')
    
    if setting is None:
        setting = QuantizationSettingFactory.default_setting()

    ppq_ir = load_graph(file_path=caffe_proto_file, 
                        caffemodel_path=caffe_model_file, 
                        from_framework=NetworkFramework.CAFFE)
    
    ppq_ir = format_graph(ppq_ir)
    ppq_ir = dispatch_graph(ppq_ir, platform, setting)

    if inputs is None:
        dummy_input = torch.zeros(size=input_shape, device=device, dtype=input_dtype)
    else: dummy_input = inputs

    quantizer = QUANTIZER_COLLECTION[platform](graph=ppq_ir)

    assert isinstance(quantizer, BaseQuantizer)
    executor = TorchExecutor(graph=quantizer._graph, device=device)
    if do_quantize:
        quantizer.quantize(
            inputs=dummy_input,
            calib_dataloader=calib_dataloader,
            executor=executor,
            setting=setting,
            calib_steps=calib_steps,
            collate_fn=collate_fn
        )
        if verbose: quantizer.report()
        return quantizer._graph
    else:
        return quantizer._graph


def export_ppq_graph(
    graph: BaseGraph, 
    platform: TargetPlatform, 
    graph_save_to: str, 
    config_save_to: str = None, 
    **kwargs) -> None:
    """
    ????????????????????? PPQ ir ?????????????????????????????? PPQ ????????????????????????
        ?????????????????? PPQ ir ???????????????????????????????????????
    this func dumps ppq IR to file, and exports quantization setting information simultaneously

    ??????????????????????????????: ppq.parser.__ini__.py
    for details please refer to ppq.parser.__ini__.py

    Args:
        graph (BaseGraph): ???????????? ir 
                           the ppq IR graph

        platform (TargetPlatform): ???????????????????????????
                           target backend platform

        graph_save_to (str): ?????????????????????????????????????????????ppq ??????????????????
                           filename to save, do not add postfix to this

        config_save_to (str): ????????????????????????????????????
            ???????????????????????????????????????????????????????????????????????????????????????????????????????????????
            note that some of platforms requires to write quantization setting
            directly into the model file, this parameter won't have effect at
            this situation
    """
    postfix = ''
    if platform in EXPORTING_POSTFIX:
        postfix = EXPORTING_POSTFIX[platform]
    graph_save_to += postfix

    for save_path in [graph_save_to, config_save_to]:
        if save_path is None: continue
        if os.path.exists(save_path):
            if os.path.isfile(save_path):
                ppq_warning(f'File {save_path} has already exist, ppq exporter will overwrite it.')
            if os.path.isdir(save_path):
                raise FileExistsError(f'File {save_path} has already exist, and it is a directory, '
                                    'ppq exporter can not create file here.')

    if platform not in EXPORTERS:
        raise KeyError(f'Requiring framework {platform} does not support export now.')
    exporter = EXPORTERS[platform]()
    assert isinstance(exporter, GraphExporter), 'Unexpected Exporter found.'
    exporter.export(file_path=graph_save_to, config_path=config_save_to, graph=graph, **kwargs)


def format_graph(graph: BaseGraph) -> BaseGraph:
    """

    ???????????????????????????????????????????????????????????????????????????????????????????????????
    ???????????????????????? cast, slice, parameter, constant ???????????????????????????????????? batchnorm ???????????????
    
    ??? PPQ ??????????????????????????? Constant ??????????????? Constant ?????????????????? parameter variable ????????????????????????
    ??? PPQ ??????????????????????????? Batchnorm ??????????????? Batchnorm ????????????
    ??? PPQ ??????????????????????????????????????????????????????????????????????????????????????????????????????
    ??? PPQ ????????????????????????????????????????????????????????????????????????
    
    This function takes pre-processing procedure with your graph.
    This function will convert operations like cast, slice, parameter, constant to the format that supported by ppq.
    This function will merge batchnorm when possible.
    
    During quantization logic, we do not expect there is any constant operation in your network, so
        all of them will be converted as parameter input variable.
    
    We do not expect there is any shared parameter in your network, all of them will be copied and spilted.
    We do not expect any isolated operation in your network, all of them will be removed.

    """

    # do graph level optimization
    formatter = GraphFormatter(GraphMerger(graph))

    formatter(GraphCommand(GraphCommandType.FORMAT_CONSTANT_INPUT))
    formatter(GraphCommand(GraphCommandType.FUSE_BN))
    formatter(GraphCommand(GraphCommandType.FORMAT_PARAMETERS))
    formatter(GraphCommand(GraphCommandType.FORMAT_CAST))
    formatter(GraphCommand(GraphCommandType.FORMAT_SLICE))
    formatter(GraphCommand(GraphCommandType.FORMAT_CLIP))
    formatter(GraphCommand(GraphCommandType.DELETE_ISOLATED))

    return graph


def dispatch_graph(graph: BaseGraph, platform: TargetPlatform, setting: QuantizationSetting) -> DispatchingTable:
    """
    
    ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
    ??????????????????????????????????????????????????????????????????????????????????????? QuantizationSetting ???????????????????????????????????????
    ???????????? PPQ ??????????????????
    
    ???????????????????????????????????????TargetPlatform ?????????????????????????????????????????????????????????????????????
    
    This function will cut your graph into a series of subgraph and send them to different device.
    PPQ provides an automatic dispatcher which, will generate different dispatching scheme on your TargetPlatform.
    A dispatching table can be passed via QuantizationSetting to override 
        the default dispatching logic of ppq dispatcher manually.

    """
    assert platform in QUANTIZER_COLLECTION, (
        f'Platform misunderstood, except one of following platform {QUANTIZER_COLLECTION.keys()}')
    quantizer = QUANTIZER_COLLECTION[platform](graph) # ??????????????? quantizer ??????????????????...
    
    if str(setting.dispatcher).lower() not in DISPATCHER_TABLE:
        raise ValueError(f'Can not found dispatcher type "{setting.dispatcher}", check your input again.')
    dispatcher = DISPATCHER_TABLE[str(setting.dispatcher).lower()]()
    assert isinstance(dispatcher, GraphDispatcher)
    assert isinstance(quantizer, BaseQuantizer)
    quant_types = quantizer.quant_operation_types

    dispatching_table = dispatcher.dispatch(
        graph=graph, quant_types=quant_types, 
        quant_platform=TargetPlatform.UNSPECIFIED, # MUST BE UNSPECIFIED, ???????????????????????? Quantizer ??????????????????????????????
        fp32_platform=TargetPlatform.FP32,         
        SOI_platform=TargetPlatform.SHAPE_OR_INDEX)

    # override dispatching result with setting
    dispatching_override = setting.dispatching_table
    for opname, platform in dispatching_override.dispatchings.items():
        if opname not in graph.operations: continue
        assert isinstance(platform, int), (
            f'Your dispatching table contains a invalid setting of operation {opname}, '
            'All platform setting given in dispatching table is expected given as int, '
            f'however {type(platform)} was given.')
        dispatching_table[opname] = TargetPlatform(platform)
    
    for operation in graph.operations.values():
        assert operation.name in dispatching_table, (
            f'Internal Error, Can not find operation {operation.name} in dispatching table.')
        operation.platform = dispatching_table[operation.name]
    
    # insert necessary device switchers.
    formatter = GraphDeviceSwitcher(graph)
    formatter(GraphCommand(GraphCommandType.INSERT_SWITCHER))
    return graph
    

class UnbelievableUserFriendlyQuantizationSetting:
    """
    ?????????????????? -- ?????????

    ????????????????????????????????????????????????
    """
    
    def __init__(self, platform: TargetPlatform, finetune_steps: int = 5000, finetune_lr: float = 3e-4,
                 interested_outputs: List[str] = None, calibration: str = 'percentile', equalization: bool = True,
                 non_quantable_op: List[str] = None) -> None:
        """
        ?????????????????? -- ?????????

        ????????????????????????????????????????????????

        Args:
            platform (TargetPlatform): ??????????????????
            finetune_steps (int, optional): ?????? finetune ??????. Defaults to 5000.
            finetune_lr (float, optional): ?????? finetune ?????????. Defaults to 3e-4.
            interested_outputs (List[str], optional): ??????finetune???variable??????????????????????????????????????????????????? op ??? variable ????????????
                ???????????????????????????????????????????????????????????????softmax??????sigmoid????????????????????????finetune???????????????????????????????????????????????????
                ???????????????variable??????????????????????????????variable????????????????????????finetune?????????????????????variable list??????????????????
            equalization (bool, optional): ???????????????????????????. Defaults to True.
            non_quantable_op (List[str], optional): ?????????????????????????????????????????????????????????????????????????????????. Defaults to None.
        """
        self.equalization     = equalization
        self.finetune_steps   = finetune_steps
        self.finetune_lr      = finetune_lr
        self.calibration      = calibration
        self.platform         = platform
        self.non_quantable_op = non_quantable_op
        self.interested_outputs = interested_outputs

        if isinstance(self.non_quantable_op, str): self.non_quantable_op = [self.non_quantable_op]
        if isinstance(self.interested_outputs, str): self.interested_outputs = [self.interested_outputs]

    def convert_to_daddy_setting(self) -> QuantizationSetting:
        # ?????????????????????????????????????????????
        daddy = QuantizationSettingFactory.default_setting()
        daddy.quantize_activation_setting.calib_algorithm = self.calibration
        
        if self.platform in {TargetPlatform.PPL_CUDA_INT4, TargetPlatform.PPL_CUDA_INT8}:
            daddy.fusion_setting.fuse_conv_add   = True
        else: daddy.fusion_setting.fuse_conv_add = False

        if self.platform in {TargetPlatform.METAX_INT8_C, TargetPlatform.METAX_INT8_T}:
            daddy.fusion_setting.force_alignment_overlap = True

        if self.finetune_steps > 0:
            daddy.advanced_optimization               = True
            daddy.advanced_optimization_setting.steps = self.finetune_steps
            daddy.advanced_optimization_setting.lr    = self.finetune_lr
            daddy.advanced_optimization_setting.limit = 2.0
            daddy.advanced_optimization_setting.interested_outputs = self.interested_outputs

        if self.equalization == True:
            daddy.equalization                    = True
            daddy.equalization_setting.iterations = 3
            daddy.equalization_setting.opt_level  = 1
            daddy.equalization_setting.value_threshold = 0

        if self.non_quantable_op is not None:
            for op_name in self.non_quantable_op:
                assert isinstance(op_name, str), (
                    f'??????????????? non_quantable_op ???????????????????????????'
                    f'non_quantable_op ?????????????????????????????????????????????????????????????????? {type(op_name)}')
                daddy.dispatching_table.append(op_name, TargetPlatform.FP32)
        
        return daddy

    def to_json(self, file_path: str) -> str:
        if os.path.exists(file_path):
            if os.path.isdir(file_path): 
                raise FileExistsError(f'?????? {file_path} ???????????????????????????????????????????????????????????????????????????')
            ppq_warning(f'?????? {file_path} ???????????????????????????')

        # TargetPlatform is not a native type, convert it to string.
        dump_dict = self.__dict__.copy()
        dump_dict['platform'] = self.platform.name

        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(obj=dump_dict, fp=file, sort_keys=True, indent=4, ensure_ascii=False)

    @ staticmethod
    def from_file(file_path: str):
        if not os.path.exists(file_path):
            raise FileNotFoundError('?????????????????????????????????????????????????????????????????????')
        with open(file_path, 'r', encoding='utf-8') as file:
            loaded = json.load(file)
        assert isinstance(loaded, dict), 'Json????????????????????????????????????'
        assert 'platform' in loaded, 'Json???????????????????????? "platform"'
        
        platform = loaded['platform']
        if platform in TargetPlatform._member_names_:
            platform = TargetPlatform._member_map_[platform]
        else: raise KeyError('??????????????????json?????????????????????????????????platform?????????')
        
        setting = UnbelievableUserFriendlyQuantizationSetting(platform)
        for key, value in loaded.items():
            if key == 'platform': continue
            if key in setting.__dict__: setting.__dict__[key] = value
            if key not in setting.__dict__: ppq_warning(f'??????Json???????????????????????????????????? {key} ???????????????????????????')
        assert isinstance(setting, UnbelievableUserFriendlyQuantizationSetting)
        return setting

    def __str__(self) -> str:
        return str(self.__dict__)


def quantize(working_directory: str, setting: QuantizationSetting, model_type: NetworkFramework,
             executing_device: str, input_shape: List[int], target_platform: TargetPlatform,
             dataloader: DataLoader, calib_steps: int = 32) -> BaseGraph:
    if model_type == NetworkFramework.ONNX:
        if not os.path.exists(os.path.join(working_directory, 'model.onnx')):
            raise FileNotFoundError(f'????????????????????????: {os.path.join(working_directory, "model.onnx")},'
                                    '???????????????caffe?????????, ?????????MODEL_TYPE???CAFFE')
        return quantize_onnx_model(
            onnx_import_file=os.path.join(working_directory, 'model.onnx'),
            calib_dataloader=dataloader, calib_steps=calib_steps, input_shape=input_shape, setting=setting,
            platform=target_platform, device=executing_device, collate_fn=lambda x: x.to(executing_device)
        )
    if model_type == NetworkFramework.CAFFE:
        if not os.path.exists(os.path.join(working_directory, 'model.caffemodel')):
            raise FileNotFoundError(f'????????????????????????: {os.path.join(working_directory, "model.caffemodel")},'
                                    '???????????????ONNX?????????, ?????????MODEL_TYPE???ONNX')
        return quantize_caffe_model(
            caffe_proto_file=os.path.join(working_directory, 'model.prototxt'),
            caffe_model_file=os.path.join(working_directory, 'model.caffemodel'),
            calib_dataloader=dataloader, calib_steps=calib_steps, input_shape=input_shape, setting=setting,
            platform=target_platform, device=executing_device, collate_fn=lambda x: x.to(executing_device)
        )


def export(working_directory: str, quantized: BaseGraph, platform: TargetPlatform, **kwargs):
    export_ppq_graph(
        graph=quantized, platform=platform,
        graph_save_to=os.path.join(working_directory, 'quantized'),
        config_save_to=os.path.join(working_directory, 'quantized.json'),
        **kwargs
    )


__all__ = ['load_graph', 'load_onnx_graph', 'load_caffe_graph',
           'dispatch_graph', 'dump_torch_to_onnx', 'quantize_onnx_model', 
           'quantize_torch_model', 'quantize_caffe_model', 
           'export_ppq_graph', 'format_graph', 'quantize', 'export', 
           'UnbelievableUserFriendlyQuantizationSetting']
