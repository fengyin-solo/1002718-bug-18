"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None
    token: str | None = None
    resumed: bool | None = None
    conflict: bool | None = None
    phase: str | None = None
    draft: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None



class BerthEntry(BaseModel):
    """泊位明细结构。"""

    field_0: str | None = None  # 泊位编号
    field_1: str | None = None  # 泊位长度
    field_2: str | None = None  # 水深条件
    field_3: str | None = None  # 可停吨位
    field_4: str | None = None  # 靠泊时段
    field_5: str | None = None  # 离泊时段
    field_6: str | None = None  # 靠泊船名
    field_7: str | None = None  # 泊位状态

class VesselEntry(BaseModel):
    """船舶明细结构。"""

    field_0: str | None = None  # 船舶编号
    field_1: str | None = None  # 船名
    field_2: str | None = None  # 船公司
    field_3: str | None = None  # 航线代码
    field_4: str | None = None  # 进口航次
    field_5: str | None = None  # 出口航次
    field_6: str | None = None  # 预计作业箱量
    field_7: str | None = None  # 船舶状态

class QuaycraneEntry(BaseModel):
    """岸桥明细结构。"""

    field_0: str | None = None  # 岸桥编号
    field_1: str | None = None  # 岸桥型号
    field_2: str | None = None  # 额定起重量
    field_3: str | None = None  # 吊具类型
    field_4: str | None = None  # 作业船舶
    field_5: str | None = None  # 作业效率
    field_6: str | None = None  # 操作司机
    field_7: str | None = None  # 岸桥状态

class YardplanEntry(BaseModel):
    """箱位明细结构。"""

    field_0: str | None = None  # 箱位编号
    field_1: str | None = None  # 所在箱区
    field_2: str | None = None  # 贝位号
    field_3: str | None = None  # 排位号
    field_4: str | None = None  # 层高上限
    field_5: str | None = None  # 当前层数
    field_6: str | None = None  # 堆放箱型
    field_7: str | None = None  # 箱位状态

class RtgEntry(BaseModel):
    """场桥明细结构。"""

    field_0: str | None = None  # 场桥编号
    field_1: str | None = None  # 场桥型号
    field_2: str | None = None  # 作业箱区
    field_3: str | None = None  # 跨距参数
    field_4: str | None = None  # 起升高度
    field_5: str | None = None  # 作业司机
    field_6: str | None = None  # 柴油油量
    field_7: str | None = None  # 场桥状态

class TruckEntry(BaseModel):
    """内集卡明细结构。"""

    field_0: str | None = None  # 集卡编号
    field_1: str | None = None  # 车牌号码
    field_2: str | None = None  # 所属车队
    field_3: str | None = None  # 当前任务
    field_4: str | None = None  # 当前位置
    field_5: str | None = None  # 司机姓名
    field_6: str | None = None  # 燃油余量
    field_7: str | None = None  # 集卡状态

class ContainerEntry(BaseModel):
    """集装箱明细结构。"""

    field_0: str | None = None  # 箱号
    field_1: str | None = None  # 箱型尺寸
    field_2: str | None = None  # 箱主代码
    field_3: str | None = None  # 毛重
    field_4: str | None = None  # 净重
    field_5: str | None = None  # 铅封号
    field_6: str | None = None  # 危品等级
    field_7: str | None = None  # 箱状态

class GateEntry(BaseModel):
    """进出闸明细结构。"""

    field_0: str | None = None  # 闸口编号
    field_1: str | None = None  # 闸口类型
    field_2: str | None = None  # 车道编号
    field_3: str | None = None  # 进出方向
    field_4: str | None = None  # OCR识别
    field_5: str | None = None  # 地磅称重
    field_6: str | None = None  # 放行抬杆
    field_7: str | None = None  # 闸口状态

class DangerousEntry(BaseModel):
    """危险品明细结构。"""

    field_0: str | None = None  # 申报编号
    field_1: str | None = None  # 箱号
    field_2: str | None = None  # 危品类别
    field_3: str | None = None  # 联合国编号
    field_4: str | None = None  # 包装等级
    field_5: str | None = None  # 积载要求
    field_6: str | None = None  # 隔离要求
    field_7: str | None = None  # 申报状态

class ColdchainEntry(BaseModel):
    """冷藏箱明细结构。"""

    field_0: str | None = None  # 冷藏箱号
    field_1: str | None = None  # 设定温度
    field_2: str | None = None  # 当前温度
    field_3: str | None = None  # 运行电流
    field_4: str | None = None  # 插电桩号
    field_5: str | None = None  # 温度偏差
    field_6: str | None = None  # 报警记录
    field_7: str | None = None  # 监控状态

class LashingEntry(BaseModel):
    """绑扎任务明细结构。"""

    field_0: str | None = None  # 绑扎编号
    field_1: str | None = None  # 对应船舶
    field_2: str | None = None  # 箱位范围
    field_3: str | None = None  # 绑扎方式
    field_4: str | None = None  # 绑扎材料
    field_5: str | None = None  # 绑扎班组
    field_6: str | None = None  # 绑扎耗时
    field_7: str | None = None  # 绑扎状态

class ShiftEntry(BaseModel):
    """工班明细结构。"""

    field_0: str | None = None  # 工班编号
    field_1: str | None = None  # 工班名称
    field_2: str | None = None  # 当班组长
    field_3: str | None = None  # 作业线数
    field_4: str | None = None  # 出勤人数
    field_5: str | None = None  # 作业时段
    field_6: str | None = None  # 作业效率
    field_7: str | None = None  # 工班状态

class RepairEntry(BaseModel):
    """修洗任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 箱号
    field_2: str | None = None  # 损伤类型
    field_3: str | None = None  # 修理等级
    field_4: str | None = None  # 修理人员
    field_5: str | None = None  # 清洗方式
    field_6: str | None = None  # 验收人员
    field_7: str | None = None  # 修洗状态

class TallyEntry(BaseModel):
    """理货记录明细结构。"""

    field_0: str | None = None  # 理货编号
    field_1: str | None = None  # 对应船舶
    field_2: str | None = None  # 箱量核对
    field_3: str | None = None  # 残损记录
    field_4: str | None = None  # 溢短记录
    field_5: str | None = None  # 理货人员
    field_6: str | None = None  # 理货时间
    field_7: str | None = None  # 理货状态

class CustomsEntry(BaseModel):
    """查验记录明细结构。"""

    field_0: str | None = None  # 查验编号
    field_1: str | None = None  # 箱号
    field_2: str | None = None  # 查验类型
    field_3: str | None = None  # 查验级别
    field_4: str | None = None  # 开箱时间
    field_5: str | None = None  # 查验结果
    field_6: str | None = None  # 封箱时间
    field_7: str | None = None  # 查验状态

class FeederEntry(BaseModel):
    """驳船明细结构。"""

    field_0: str | None = None  # 驳船编号
    field_1: str | None = None  # 驳船名称
    field_2: str | None = None  # 运营公司
    field_3: str | None = None  # 载箱量
    field_4: str | None = None  # 到港时间
    field_5: str | None = None  # 离港时间
    field_6: str | None = None  # 靠泊码头
    field_7: str | None = None  # 驳船状态

class OogEntry(BaseModel):
    """超限箱明细结构。"""

    field_0: str | None = None  # 超限箱号
    field_1: str | None = None  # 箱型尺寸
    field_2: str | None = None  # 超限方向
    field_3: str | None = None  # 超限尺寸
    field_4: str | None = None  # 专用吊具
    field_5: str | None = None  # 堆放区域
    field_6: str | None = None  # 绑扎方案
    field_7: str | None = None  # 超限状态

class EmptystackEntry(BaseModel):
    """空箱明细结构。"""

    field_0: str | None = None  # 空箱编号
    field_1: str | None = None  # 箱主代码
    field_2: str | None = None  # 箱型尺寸
    field_3: str | None = None  # 箱体状况
    field_4: str | None = None  # 进场日期
    field_5: str | None = None  # 堆存天数
    field_6: str | None = None  # 出场日期
    field_7: str | None = None  # 空箱状态

class EnergyEntry(BaseModel):
    """能耗记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 设备类型
    field_2: str | None = None  # 设备编号
    field_3: str | None = None  # 电耗度数
    field_4: str | None = None  # 油耗升数
    field_5: str | None = None  # 记录时段
    field_6: str | None = None  # 抄表人员
    field_7: str | None = None  # 能耗状态

class SafetycheckEntry(BaseModel):
    """巡检记录明细结构。"""

    field_0: str | None = None  # 巡检编号
    field_1: str | None = None  # 巡检区域
    field_2: str | None = None  # 巡检日期
    field_3: str | None = None  # 巡检人员
    field_4: str | None = None  # 发现隐患
    field_5: str | None = None  # 整改措施
    field_6: str | None = None  # 整改期限
    field_7: str | None = None  # 巡检状态
