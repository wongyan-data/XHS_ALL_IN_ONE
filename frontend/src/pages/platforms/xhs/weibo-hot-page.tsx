import { useEffect, useState } from "react";
import { Table, Button, Card, Row, Col, Typography, Drawer, Space, Spin, Checkbox, Input, message, Alert, Image, Tag } from "antd";
import { FireOutlined, EyeOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { fetchWeiboHotSearch, fetchWeiboTweets, generateDraftFromWeiboHot } from "../../../lib/api";
import type { WeiboHotSearchItem, WeiboTweet } from "../../../lib/api";

const { Title, Text, Paragraph } = Typography;

export function WeiboHotSearchPage() {
  const navigate = useNavigate();
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [hotItems, setHotItems] = useState<WeiboHotSearchItem[]>([]);
  
  const [selectedWord, setSelectedWord] = useState<string | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [loadingTweets, setLoadingTweets] = useState(false);
  const [tweets, setTweets] = useState<WeiboTweet[]>([]);
  
  // Selection states
  const [selectedTweets, setSelectedTweets] = useState<string[]>([]);
  const [selectedImages, setSelectedImages] = useState<string[]>([]);
  
  // AI generation prompt
  const [aiPrompt, setAiPrompt] = useState(
    "将此热搜主题改写为一篇吸引人的小红书图文笔记。风格活泼、口语化，使用大量适宜的表情符号增加趣味，并自动推荐3-5个小红书话题。"
  );
  const [generating, setGenerating] = useState(false);

  // Load hot search on mount
  useEffect(() => {
    void loadHotSearch();
  }, []);

  async function loadHotSearch() {
    setLoadingSearch(true);
    try {
      const res = await fetchWeiboHotSearch();
      setHotItems(res.items);
    } catch (err) {
      void message.error("获取微博热搜失败");
    } finally {
      setLoadingSearch(false);
    }
  }

  async function handleOpenDetail(word: string) {
    setSelectedWord(word);
    setIsDrawerOpen(true);
    setLoadingTweets(true);
    setTweets([]);
    setSelectedTweets([]);
    setSelectedImages([]);
    try {
      const res = await fetchWeiboTweets(word);
      setTweets(res.items);
      
      // Select all tweets and images by default
      setSelectedTweets(res.items.map(t => t.text));
      const allImages: string[] = [];
      res.items.forEach(t => {
        allImages.push(...t.image_urls);
      });
      setSelectedImages(allImages);
    } catch (err) {
      void message.error("检索微博推文背景失败");
    } finally {
      setLoadingTweets(false);
    }
  }

  async function handleGenerate() {
    if (!selectedWord) return;
    setGenerating(true);
    try {
      await generateDraftFromWeiboHot({
        word: selectedWord,
        instruction: aiPrompt,
        reference_tweets: selectedTweets,
        image_urls: selectedImages
      });
      void message.success("小红书草稿生成成功，正在跳转到草稿箱...");
      // Redirect after a short delay
      setTimeout(() => {
        navigate("/platforms/xhs/drafts");
      }, 1000);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || "生成草稿失败";
      void message.error(detail);
    } finally {
      setGenerating(false);
    }
  }

  const columns = [
    {
      title: "排名",
      dataIndex: "rank",
      key: "rank",
      width: 80,
      render: (rank: number) => {
        let color = "#8c8c8c";
        if (rank === 1) color = "#ff4d4f";
        else if (rank === 2) color = "#ff7a45";
        else if (rank === 3) color = "#ffa940";
        return (
          <span style={{ fontWeight: "bold", color, fontSize: 16 }}>
            {rank}
          </span>
        );
      }
    },
    {
      title: "热搜词",
      dataIndex: "word",
      key: "word",
      render: (word: string) => (
        <span style={{ fontWeight: 500, color: "#177ddc" }}>
          {word}
        </span>
      )
    },
    {
      title: "热度",
      dataIndex: "num",
      key: "num",
      width: 150,
      render: (num: number) => (num > 0 ? `${(num / 10000).toFixed(1)}W` : "-")
    },
    {
      title: "类型",
      dataIndex: "label",
      key: "label",
      width: 100,
      render: (label: string) => {
        if (!label) return null;
        let color = "blue";
        if (label === "热") color = "red";
        else if (label === "新") color = "green";
        else if (label === "爆") color = "magenta";
        return <Tag color={color}>{label}</Tag>;
      }
    },
    {
      title: "操作",
      key: "action",
      width: 150,
      render: (_: any, record: WeiboHotSearchItem) => (
        <Button
          type="primary"
          ghost
          icon={<EyeOutlined />}
          onClick={(e) => {
            e.stopPropagation();
            void handleOpenDetail(record.word);
          }}
        >
          背景推文
        </Button>
      )
    }
  ];

  return (
    <div style={{ padding: 24 }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <FireOutlined style={{ color: "#ff4d4f", marginRight: 8 }} />
            微博热搜采集
          </Title>
          <Text type="secondary">
            实时采集微博热点事件，抓取前因后果与原图，一键使用 AI 改写为带标签和素材的小红书图文笔记。
          </Text>
        </Col>
        <Col>
          <Button onClick={() => void loadHotSearch()} loading={loadingSearch}>
            刷新热搜
          </Button>
        </Col>
      </Row>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={hotItems}
          columns={columns}
          rowKey="word"
          loading={loadingSearch}
          pagination={{ pageSize: 50, hideOnSinglePage: true }}
          onRow={(record) => ({
            onClick: () => {
              void handleOpenDetail(record.word);
            },
            style: { cursor: "pointer" }
          })}
        />
      </Card>

      <Drawer
        title={
          <span style={{ display: "flex", alignItems: "center" }}>
            <FireOutlined style={{ color: "#ff4d4f", marginRight: 6 }} />
            热搜事件原文背景：{selectedWord}
          </span>
        }
        placement="right"
        width={720}
        onClose={() => setIsDrawerOpen(false)}
        open={isDrawerOpen}
        styles={{ body: { background: "#1a1a1a" } }}
      >
        {loadingTweets ? (
          <div style={{ textAlign: "center", padding: "100px 0" }}>
            <Spin size="large" tip="正在抓取微博原文背景..." />
          </div>
        ) : (
          <Space direction="vertical" size={24} style={{ width: "100%" }}>
            <div>
              <Title level={5} style={{ marginBottom: 12, color: "#e8e8e8" }}>
                1. 勾选参考背景推文 (AI 会阅读这些内容)
              </Title>
              {tweets.length === 0 ? (
                <Alert message="未找到该热搜对应的热门推文内容。" type="warning" showIcon />
              ) : (
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  {tweets.map((tweet) => {
                    const isSelected = selectedTweets.includes(tweet.text);
                    return (
                      <Card
                        key={tweet.id}
                        size="small"
                        styles={{ body: { padding: 12 } }}
                        style={{
                          border: isSelected ? "1px solid #177ddc" : "1px solid #303030",
                          background: "#262626"
                        }}
                      >
                        <Row justify="space-between" align="middle" style={{ marginBottom: 8 }}>
                          <Col>
                            <Text strong style={{ color: "#177ddc" }}>
                              @{tweet.author}
                            </Text>
                            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                              {tweet.created_at}
                            </Text>
                          </Col>
                          <Col>
                            <Checkbox
                              checked={isSelected}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  setSelectedTweets(prev => [...prev, tweet.text]);
                                } else {
                                  setSelectedTweets(prev => prev.filter(t => t !== tweet.text));
                                }
                              }}
                            >
                              参考此条
                            </Checkbox>
                          </Col>
                        </Row>
                        <Paragraph style={{ margin: 0, whiteSpace: "pre-wrap", color: "#d9d9d9" }}>
                          {tweet.text}
                        </Paragraph>

                        {tweet.image_urls.length > 0 && (
                          <div style={{ marginTop: 12 }}>
                            <Space size={8} wrap>
                              {tweet.image_urls.map((url) => {
                                const imgSelected = selectedImages.includes(url);
                                return (
                                  <div
                                    key={url}
                                    style={{
                                      position: "relative",
                                      border: imgSelected ? "2px solid #177ddc" : "2px solid transparent",
                                      borderRadius: 4,
                                      overflow: "hidden",
                                      cursor: "pointer"
                                    }}
                                    onClick={() => {
                                      if (imgSelected) {
                                        setSelectedImages(prev => prev.filter(i => i !== url));
                                      } else {
                                        setSelectedImages(prev => [...prev, url]);
                                      }
                                    }}
                                  >
                                    <Image
                                      src={url}
                                      width={80}
                                      height={80}
                                      preview={false}
                                      style={{ objectFit: "cover" }}
                                    />
                                    {imgSelected && (
                                      <div
                                        style={{
                                          position: "absolute",
                                          top: 2,
                                          right: 2,
                                          background: "#177ddc",
                                          borderRadius: "50%",
                                          width: 16,
                                          height: 16,
                                          display: "flex",
                                          alignItems: "center",
                                          justifyContent: "center"
                                        }}
                                      >
                                        <CheckCircleOutlined style={{ fontSize: 10, color: "#fff" }} />
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </Space>
                          </div>
                        )}
                      </Card>
                    );
                  })}
                </Space>
              )}
            </div>

            <div>
              <Title level={5} style={{ marginBottom: 12, color: "#e8e8e8" }}>
                2. AI 改写小红书指令微调
              </Title>
              <Input.TextArea
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                rows={4}
                placeholder="在此输入 AI 改写指令..."
              />
            </div>

            <Button
              type="primary"
              size="large"
              block
              loading={generating}
              disabled={tweets.length === 0 || selectedTweets.length === 0}
              onClick={() => void handleGenerate()}
            >
              {generating ? "正在调用 AI 大模型进行图文改写..." : "一键改写并导入草稿工坊"}
            </Button>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
