from device import device
import uiautomator2 as u2
import numpy as np


class state(device):
    def __init__(self, device: u2.Device):
        super().__init__(device)

    def get_state(self):
        img = self.capture_screenshot()
        conditions = [
            abs(np.sum(img[726, 223]) - np.sum([173, 112, 68])) < 10,
            abs(np.sum(img[732, 423]) - np.sum([42, 155, 111])) < 10,
            abs(np.sum(img[270, 194]) - np.sum([46, 78, 119])) < 10,
            abs(np.sum(img[407, 142]) - np.sum([106, 194, 254])) < 10,
            abs(np.sum(img[414, 309]) - np.sum([60, 102, 208])) < 10,
            abs(np.sum(img[276, 379]) - np.sum([44, 73, 112])) < 10,
            abs(np.sum(img[271, 452]) - np.sum([104, 154, 182])) < 10,
            abs(np.sum(img[165, 302]) - np.sum([55, 105, 157])) < 10,
            abs(np.sum(img[569, 233]) - np.sum([201, 227, 233])) < 10,
            abs(np.sum(img[719, 23]) - np.sum([80, 95, 111])) < 10,
            abs(np.sum(img[702, 515]) - np.sum([82, 98, 111])) < 10
        ]
        if all(conditions):
            return '放置獎勵'
        elif all(
            [ np.sum(img[765,175])-np.sum([186,189,187])<10, np.sum(img[807,107])-np.sum([0,0,0])<10, np.sum(img[500,95])-np.sum([0,0,0])<10, np.sum(img[416,431])-np.sum([0,0,0])<10, np.sum(img[144,451])-np.sum([0,0,0])<10, np.sum(img[97,67])-np.sum([0,0,0])<10, np.sum(img[287,533])-np.sum([0,0,0])<10, np.sum(img[481,461])-np.sum([0,0,0])<10, np.sum(img[209,109])-np.sum([0,0,0])<10, np.sum(img[343,71])-np.sum([0,0,0])<10, np.sum(img[837,365])-np.sum([0,0,0])<10, ]
        ):
            return '滑動解除節電模式'
        elif all(
           [
        np.sum(img[346,192])-np.sum([39,50,78])<10,
        np.sum(img[183,178])-np.sum([33,45,73])<10,
        np.sum(img[192,288])-np.sum([227,243,255])<10,
        np.sum(img[201,292])-np.sum([222,241,248])<10,
        np.sum(img[198,264])-np.sum([223,239,255])<10,
        np.sum(img[202,251])-np.sum([149,166,179])<10,
        np.sum(img[832,270])-np.sum([30,47,168])<10,
        np.sum(img[823,261])-np.sum([34,51,168])<10,
        np.sum(img[841,280])-np.sum([18,33,143])<10,
        np.sum(img[842,261])-np.sum([26,32,167])<10,
        np.sum(img[822,282])-np.sum([24,43,140])<10,
        np.sum(img[833,285])-np.sum([183,226,243])<10,
        np.sum(img[832,261])-np.sum([170,213,252])<10,
        np.sum(img[846,272])-np.sum([180,227,231])<10,
        np.sum(img[195,34])-np.sum([63,82,117])<10,
        np.sum(img[194,59])-np.sum([65,84,117])<10
    ]
):  return '公告'
        elif all(
            [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10,abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10,abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10,abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10,abs(np.sum(img[908, 223]) - np.sum([160, 165, 164])) <= 10,abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10,abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10,abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10,abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]
        ):  return '主頁'
        return 'unknown'