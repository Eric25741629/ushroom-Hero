def find_by_point(d):
    img = d.screenshot(format='opencv')
    if list(img[709, 47]) == [165, 194, 221] and list(img[723, 343]) == [160, 190, 218]:
        return "main_page"
    if list(img[723, 343]) == [73, 86, 100] and list(img[709, 47]) == [84, 156, 107]:
        return "homeland"
    if list(img[723, 343]) == [115, 103, 230] and list(img[709, 47]) == [196, 192, 201]:
        return "skill_page"
    if list(img[723, 343]) == [104, 178, 80] and list(img[709, 47]) == [196, 192, 201]:
        return "partner_page"
    if list(img[193, 201]) == [42, 40, 64] and list(img[53, 453]) == [40, 37, 55]:
        return "ore"
    if list(img[145, 42]) == [98, 87, 96] and list(img[74, 497]) == [77, 68, 81]:
        return "follower"
    if list(img[341, 282]) == [130, 192, 236] and list(img[357, 294]) == [133, 196, 239]:
        return "garden"
    if list(img[666, 68]) == [252, 250, 249] and list(img[721, 483]) == [255, 255, 255]:
        return "park"
    if list(img[49, 357]) == [55, 68, 193]:
        return "instance"
    return "unknown"
