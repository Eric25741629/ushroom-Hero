import numpy as np
red_mask_lower = np.array([0, 210, 184], dtype="uint8")
red_mask_upper = np.array([5, 255, 255], dtype="uint8")

green_mask_lower = np.array([30, 36, 68], dtype="uint8")
green_mask_upper = np.array([56, 236, 217], dtype="uint8")

hr_mask_lower = np.array([0, 165, 168], dtype="uint8")
hr_mask_upper = np.array([6, 199, 212], dtype="uint8")

img = np.zeros((1000, 1000, 3), dtype="uint8")
home_land_conditions = [abs(np.sum(img[502, 284]) - np.sum([228, 217, 59])) <= 10, abs(np.sum(img[511, 79]) - np.sum([186, 154, 47])) <= 10, abs(np.sum(img[620, 87]) - np.sum([87, 166, 205])) <= 10, abs(np.sum(img[377, 384]) - np.sum([91, 120, 157])) <=
                        10, abs(np.sum(img[239, 263]) - np.sum([95, 152, 119])) <= 10, abs(np.sum(img[71, 166]) - np.sum([102, 122, 103])) <= 10, abs(np.sum(img[810, 82]) - np.sum([157, 203, 244])) <= 10, abs(np.sum(img[798, 322]) - np.sum([190, 186, 54])) <= 10]
home_page = [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10, abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10, abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10, abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10, abs(np.sum(img[908, 223]) -
                                                                                                                                                                                                                                                          np.sum([160, 165, 164])) <= 10, abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10, abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10, abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10, abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]
